# -*- coding: utf-8 -*-
"""DSH SDK 桥：用官方 deepseek-harness-sdk 驱动本地 dsh（--profile sdk）。

替代旧的 dsh_client.py（逆向 HTTP/WS 网关协议 + 私有 web Host）：

- 运行时：dsh_bin=本地 dsh 的 .cmd 包装（node bin.js），profile=sdk（dsh-base 全量工具树）
- 事件：session.event 通知实时回调（step/tool/assistant 全量持久事件），session.status 判定回合结束
- MCP：patches 传 voice_runtime.patch.yml，挂 dsh-mcp-client → dsh_voice_mcp.py（speak 工具 → app 内 TTS）
- 会话：voice_session.id 持久化。0.1.5rc1 的 SDK server 不支持跨进程 resume，
  首次 prompt 收到 "already exists" 时自动换新会话并写回文件（跨重启丢历史，属已知取舍）
- 权限：隔离 home 的 settings.yaml 固定 danger-full-access（纯语音场景无法逐条审批）

约束（实测结论）：
- 必须用本地 dsh（捆绑单文件运行时的 pwsh 工具损坏）
- on_notification 在本线程同步回调；prompt() 阻塞到回合 idle
- 回合串行（_turn_lock）：避免双回合的卡片事件交错
"""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Callable

from deepseek_harness import DeepSeekHarness, Notification
from deepseek_harness.errors import JsonRpcError

APP_DIR = Path(__file__).resolve().parent
DSH_BIN = str(APP_DIR / "dsh_local.cmd")
HOME = APP_DIR / "_voice_home"
PATCH = APP_DIR / "voice_runtime.patch.yml"
WORKSPACE = str(Path.home() / ".dsh")   # 与旧 Host 的 cwd 保持一致（语音任务的产出落点不变）
SESSION_STORE = APP_DIR / "voice_session.id"
DEBUG_LOG = APP_DIR / "app_debug.log"

# 权限预设（写进隔离 home 的 settings.yaml，勿动格式）
_SETTINGS_YAML = (
    "# 语音 SDK 运行时：自主执行，不逐条审批（纯语音场景无法逐个确认）\n"
    "permission:\n"
    "  defaultPreset: danger-full-access\n"
)


def _dbg(msg: str) -> None:
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{__import__('time').strftime('%H:%M:%S')} [sdk] {msg}\n")
    except OSError:
        pass


def _load_deepseek_key() -> str:
    import os
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env = APP_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


class DshSdkBridge:
    """封装 DeepSeekHarness 生命周期 + 会话回退 + 回合串行。

    可参数化 home/workspace/session_store：测试跑器用独立 home 与 app 实例隔离。
    """

    def __init__(self, home: "str | Path | None" = None,
                 workspace: "str | Path | None" = None,
                 session_store: "str | Path | None" = None) -> None:
        self._home = Path(home) if home else HOME
        self._workspace = str(Path(workspace)) if workspace else WORKSPACE
        self._session_store = Path(session_store) if session_store else SESSION_STORE
        self._harness: DeepSeekHarness | None = None
        self._ready = threading.Event()
        self._turn_lock = threading.Lock()
        self._session_id: str | None = None

    # ---- 启动 ----
    def ensure_ready(self) -> str:
        """启动 runtime 并完成 initialize（幂等）。返回会话 id 或 ""。"""
        if self._ready.is_set():
            return self._session_id or ""
        self._kill_stale_runtime()
        self._prepare_home(self._home)
        key = _load_deepseek_key()
        if not key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY（app/.env 或环境变量）")
        self._harness = DeepSeekHarness(
            dsh_home=str(self._home),
            cwd=self._workspace,
            provider="deepseek-official",
            model="deepseek-v4-flash",
            api_key=key,
            max_tokens=16384,
            # 语音场景降档实验：压首声延迟（c06 曾 17s 才首声，high 档嫌疑）。
            # 已探测：deepseek-v4-flash 仅支持 low/high，无 medium。
            reasoning_effort="low",
            dsh_bin=DSH_BIN,
            patches=(str(PATCH),),
            initialize_timeout_seconds=120,
            request_timeout_seconds=600,
        )
        self._harness.start()  # 阻塞到 initialize 完成（含模型路由校验）
        try:
            sid = self._session_store.read_text(encoding="utf-8").strip() or None
        except OSError:
            sid = None
        self._session_id = sid
        self._ready.set()
        _dbg(f"SDK 就绪 sid={sid}")
        return sid or ""

    @staticmethod
    def _kill_stale_runtime() -> None:
        """扫杀残留的语音 runtime 进程（node/cmd 起 sdk profile 的孤儿）。

        教训：SDK 传输崩坏时 close() 只 terminate cmd 外壳，node 子进程变孤儿
        并持锁占住 home；之后每次启动的 runtime 都会撞锁、输出 GBK 警告，
        把 SDK 的 utf-8 管道再次弄崩 → 死循环。启动前必须先清。
        """
        try:
            import subprocess
            ps = (
                "Get-CimInstance Win32_Process -Filter \"name='node.exe' OR name='cmd.exe'\" | "
                "Where-Object { $_.CommandLine -match 'dsh.lib.bin.js' -and "
                "$_.CommandLine -match 'profile sdk' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        except Exception as exc:
            _dbg(f"扫杀残留 runtime 失败: {exc!r}")

    @staticmethod
    def _prepare_home(home: "Path | None" = None) -> None:
        """隔离 home：首次启动物化 sdk profile 并写入权限预设。

        空 home 下本地 dsh 自举 profile 会在 stdout 输出本地编码（GBK）的
        噪点字节，把 SDK 的 utf-8 文本管道弄崩（"runtime stdout closed"）。
        先用捆绑运行时 --dump-default-config 物化（官方教程同款初始化），
        之后本地 dsh 静默启动。
        """
        h = Path(home) if home else HOME
        try:
            h.mkdir(parents=True, exist_ok=True)
            settings = h / "settings.yaml"
            if not settings.exists():
                settings.write_text(_SETTINGS_YAML, encoding="utf-8")
            if not (h / "profiles" / "sdk").exists():
                import subprocess
                try:
                    from deepseek_harness_runtime import resolve_bundled_launch_args
                    exe = list(resolve_bundled_launch_args())
                except ImportError:
                    exe = []
                if exe:
                    env = dict(os.environ, DSH_HOME=str(h))
                    subprocess.run(
                        [exe[0], "--profile", "sdk", "--dump-default-config"],
                        env=env, capture_output=True, timeout=180)
        except OSError:
            pass
        except Exception as exc:
            _dbg(f"home 物化失败(继续尝试): {exc!r}")

    # ---- 回合 ----
    def prompt(
        self,
        text: str,
        on_event: Callable[[str, dict], None],
        on_status: Callable[[str, dict], None],
        session_id: str | None = None,
    ) -> None:
        """发送一个提示并阻塞到回合 idle；事件经回调实时投递（本线程同步）。

        on_event(event_type, data)：session.event 的持久事件
        on_status(status, payload)：session.status（running/idle）
        session_id：可选指定会话（测试跑器用）；缺省用持久化的语音会话
        """
        self.ensure_ready()
        sid = session_id or self._session_id
        with self._turn_lock:
            for attempt in range(2):
                try:
                    self._run_turn(text, sid, on_event, on_status)
                    return
                except JsonRpcError as exc:
                    # 跨进程 resume 不支持：换新会话重发一次（历史丢失，见模块注释）
                    if "already exists" in str(exc) and attempt == 0 and session_id is None:
                        _dbg(f"会话 {sid} 不可 resume，换新会话")
                        self._rotate_session()
                        sid = self._session_id
                        continue
                    raise

    def _run_turn(self, text, sid, on_event, on_status) -> None:
        assert self._harness and sid

        def cb(n: Notification) -> None:
            p = n.payload or {}
            # 只投影根会话：subagent 的事件/状态不进浮窗（与旧 follow 行为一致）
            if p.get("sessionId") != sid:
                return
            if n.method == "session.event":
                ev = p.get("event") or {}
                if isinstance(ev, dict):
                    try:
                        on_event(str(ev.get("type")), ev.get("data") or {})
                    except Exception as exc:  # 回调异常不能杀回合
                        _dbg(f"on_event 异常 {ev.get('type')}: {exc!r}")
            elif n.method == "session.status":
                try:
                    on_status(str(p.get("status")), p)
                except Exception as exc:
                    _dbg(f"on_status 异常: {exc!r}")

        self._harness.run(text, session_id=sid, on_notification=cb)

    def _rotate_session(self) -> None:
        new_id = "session-" + uuid.uuid4().hex[:16]
        try:
            self._session_store.write_text(new_id, encoding="utf-8")
        except OSError:
            pass
        self._session_id = new_id
        _dbg(f"新语音会话 {new_id}")

    def nudge(self, text: str) -> None:
        """回合进行中往 inbox 塞一条提醒（不抢回合锁）。

        用途：长任务心跳——提醒 agent 用 speak 播报当前进度。
        消息会在下一步边界被 agent 收下处理，不影响进行中的工具执行。
        """
        if not self._ready.is_set() or not self._session_id or self._harness is None:
            return
        try:
            self._harness.client.session_prompt(
                self._session_id, [{"type": "text", "text": text}])
        except Exception as exc:
            _dbg(f"心跳 nudge 失败: {exc!r}")

    # ---- 生命周期 ----
    def close(self) -> None:
        if self._harness is not None:
            try:
                self._harness.close()
            except Exception:
                pass
            self._harness = None
        self._ready.clear()