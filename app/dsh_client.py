# -*- coding: utf-8 -*-
"""DSH Host API 客户端（Python 直连 Host，不经浏览器 UI 注入）。

协议（逆向自 @deepseek-ai/dsh-api-gateway + dsh-client-connection）：

- 鉴权：每个 Host 进程生成随机启动 token，打印带 ?token= 的根 URL；
  GET /?token=xxx → Set-Cookie 签名会话 cookie（HttpOnly, SameSite=Strict,
  绑定 authority，默认 30 天）；之后 RPC 与 WS 都凭该 cookie。
- 一元调用：POST /api/<endpoint>
    请求  {"type":"client-request","rpcId":"<uuid>","method":"<endpoint>",
          "payload":{"args":{...}}}
    响应  {"type":"server-response","rpcId":...,
          "result":{"ok":true,"value":...} | {"ok":false,"error":{code,message,details}}}
- 流式：WS /api/remote.mux（同一 cookie；Host 每 ~2s 发 Ping，库自动回 Pong）
    C→S {"type":"open","streamId","endpoint","payload":{"args":{...}}}
        {"type":"cancel","streamId"}
    S→C {"type":"item","streamId","value"} | {"type":"end","streamId"}
        | {"type":"error","streamId","error":{code,message,details}}

用到的 endpoint（生成描述符见 dsh-api-session-controller/lib/typert.remote-client.js）：
    session/list    {_request:{cursor?}} → {items:[{sessionId,updatedAt,cwd,origin?,...}]}
    session/create  {request:{workspaceId?,cwd?,...}} → {sessionId}
    session/prompt  {request:{requestId,sessionId,mode,content:[{type:'text',text}]}} → {accepted:true}
    session/follow  (stream) {request:{address:{kind:'session',sessionId},maxMessages?,assistantStream?}}
                    → frames: snapshot / event({type:'event',event:{type,seq,data}}) /
                      assistant-stream({frame:{type:'start'|'chunk'|'end', chunk?{type:'text-delta',text}}})
"""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Callable
from urllib.parse import urlparse

_DSH_BIN = r"C:\Users\60512\.dsh\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js"
_DSH_HOME = r"C:\Users\60512\.dsh"
_URL_RE = re.compile(r"http://127\.0\.0\.1:(\d+)/\?token=([^\s]+)")
_HOST_PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dsh_host.pid")


class DshError(RuntimeError):
    """Host 返回的业务错误（result.ok=false）或装配错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class DshClient:
    """线程安全；内部用 http.client（同步）+ websockets.sync（独立线程跑 follow）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # 串行化一元调用（http.client 非线程安全复用）
        self._proc: subprocess.Popen | None = None
        self._base: str | None = None      # http://127.0.0.1:PORT
        self._cookie: str | None = None
        self._follow_ws = None             # 当前 follow 的 WS（close_follow 用）
        self._ready = threading.Event()

    # ---- Host 生命周期 ----
    def ensure_host(self, on_log: Callable[[str], None] = print) -> None:
        """确保 DSH Web Host 在跑；不在就跑 node dsh web 并解析 token URL。

        用 --port 0 让 OS 分空闲端口：用户可能已有一个 dsh web 实例占着 3080，
        我们起自己的私有 Host（token 只有我们能拿到）。私有 Host 用户感知不到，
        退出时顺手杀掉，避免残留进程累积。
        """
        if self._ready.is_set():
            return
        if self._proc is not None and self._proc.poll() is None:
            self._ready.wait(timeout=30)
            return
        self._kill_stale_host()  # 清理上次异常退出（强杀 app）留下的孤儿 Host
        env = dict(os.environ, DSH_HOME=_DSH_HOME)
        self._proc = subprocess.Popen(
            ["node", _DSH_BIN, "web", "--no-open", "--port", "0"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env=env, cwd=_DSH_HOME,
        )
        try:
            with open(_HOST_PID_FILE, "w") as f:
                f.write(str(self._proc.pid))
        except OSError:
            pass
        import atexit
        atexit.register(self._kill_host)
        on_log(f"[dsh] 拉起 Host PID={self._proc.pid}")
        threading.Thread(target=self._read_stdout, args=(on_log,), daemon=True).start()
        if not self._ready.wait(timeout=60):
            raise DshError("host/timeout", "DSH Host 60s 内未就绪")

    @staticmethod
    def _kill_stale_host() -> None:
        """按 pid 文件杀上次残留的私有 Host（会话写句柄被占的 SessionAlreadyOwned 根因）。

        只杀命令行里确实是 dsh web 的进程，防 PID 复用误杀。
        """
        try:
            with open(_HOST_PID_FILE) as f:
                pid = int(f.read().strip())
        except (OSError, ValueError):
            return
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
                capture_output=True, text=True, timeout=10,
            ).stdout or ""
            if "dsh" in out and "web" in out:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                               capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass

    def _kill_host(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except OSError:
            pass
        try:
            os.remove(_HOST_PID_FILE)
        except OSError:
            pass

    def _read_stdout(self, on_log: Callable[[str], None]) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            m = _URL_RE.search(line)
            if m and not self._ready.is_set():
                self._base = f"http://127.0.0.1:{m.group(1)}"
                try:
                    self._authorize(m.group(1), m.group(2))
                    self._ready.set()
                    on_log(f"[dsh] Host 就绪 {self._base}（已取会话 cookie）")
                except Exception as exc:
                    on_log(f"[dsh] 鉴权失败: {exc}")
            # Host 日志全部透传到应用日志（排查用）
            print(f"[dsh web] {line}", flush=True)

    def _authorize(self, port: str, token: str) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=10)
        conn.request("GET", f"/?token={token}")
        resp = conn.getresponse()
        resp.read()  # 丢弃 body（重定向页）
        cookies = resp.getheader("Set-Cookie") or ""
        conn.close()
        if resp.status not in (301, 302, 303, 307, 308, 200) or not cookies:
            raise DshError("auth/rejected", f"GET /?token 返回 {resp.status}")
        # Set-Cookie: name=value; Path=/; HttpOnly; ... → 只取 name=value
        self._cookie = cookies.split(";", 1)[0].strip()

    # ---- 一元调用 ----
    def call(self, endpoint: str, args: dict, timeout: float = 30.0):
        if not self._ready.is_set():
            raise DshError("host/down", "DSH Host 未就绪")
        assert self._base and self._cookie
        rpc_id = uuid.uuid4().hex
        body = json.dumps({
            "type": "client-request",
            "rpcId": rpc_id,
            "method": endpoint,
            "payload": {"args": args},
        }, ensure_ascii=False).encode("utf-8")
        url = urlparse(self._base)
        with self._lock:
            conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
            try:
                conn.request("POST", f"/api/{endpoint}", body=body, headers={
                    "Content-Type": "application/json",
                    "Cookie": self._cookie,
                })
                resp = conn.getresponse()
                raw = resp.read()
            finally:
                conn.close()
        if resp.status != 200:
            raise DshError("gateway/transport", f"HTTP {resp.status}: {raw[:200]!r}")
        env = json.loads(raw)
        if env.get("type") != "server-response" or env.get("rpcId") != rpc_id:
            raise DshError("gateway/envelope", f"响应包络异常: {raw[:200]!r}")
        result = env["result"]
        if not result.get("ok"):
            err = result.get("error") or {}
            raise DshError(err.get("code", "?"), err.get("message", ""))
        return result.get("value")

    # ---- 会话解析与发消息 ----
    def resolve_session(self) -> str:
        """取最近活跃的非 subagent 会话；没有则新建一个。"""
        value = self.call("session/list", {"_request": {}})
        items = [s for s in value.get("items", []) if s.get("origin") != "subagent"]
        if items:
            items.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
            return items[0]["sessionId"]
        created = self.call("session/create", {"request": {}})
        return created["sessionId"]

    # ---- P0 自主化：权限预设 + 专属语音会话 ----
    def describe_settings(self) -> dict:
        """读取全部设置命名空间（验证用）。"""
        return self.call("settings/describe", {})

    def set_permission_preset(self, preset: str = "danger-full-access") -> None:
        """把 permission.defaultPreset 设为指定预设（只作用于之后新建的会话）。

        内置预设（dsh-permission-presets）：
        - workspace-write      沙箱 workspace-write + 审批 ask（默认，逐条确认）
        - danger-full-access   无沙箱 + 审批 never（语音场景用它消除确认打断）
        """
        self.call("settings/update", {
            "ns": "permission",
            "patch": {"defaultPreset": preset},
        })

    def resolve_voice_session(self, store: "os.PathLike | str") -> str:
        """复用持久化的专属语音会话；不存在/已失效则新建（新建时固定当前权限预设）。

        为什么不用 resolve_session：最近活跃会话可能创建于 danger-full-access
        设置之前，仍带 approval=ask；专属语音会话在创建时即固定自主权限，
        并跨 app 重启复用以保持上下文连续。
        """
        from pathlib import Path
        store = Path(store)
        sid = None
        try:
            sid = store.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
        if sid:
            try:
                value = self.call("session/list", {"_request": {}})
                if any(s.get("sessionId") == sid for s in value.get("items", [])):
                    return sid
            except Exception:
                pass  # 列表失败则直接新建
        created = self.call("session/create", {"request": {}})
        new_id = created["sessionId"]
        try:
            store.write_text(new_id, encoding="utf-8")
        except OSError:
            pass
        return new_id

    def prompt(self, session_id: str, text: str) -> None:
        self.call("session/prompt", {"request": {
            "requestId": uuid.uuid4().hex,
            "sessionId": session_id,
            "mode": "queue",
            "content": [{"type": "text", "text": text}],
        }})

    def close_follow(self) -> None:
        """从外部关闭当前 follow 流（follow 循环会抛断连异常后按新会话重连）。

        场景：会话被别的 Host 占（SessionAlreadyOwned）→ 换新会话后要重启 follow。
        """
        ws = self._follow_ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    # ---- 会话流（follow）----
    def follow(
        self,
        session_id: str,
        on_frame: Callable[[dict], bool | None],
        on_log: Callable[[str], None] = print,
    ) -> None:
        """在当前线程跑 follow 流（调用方应放工作线程）。

        on_frame(value) 收到每个 stream item；返回 False 提前终止（发 cancel）。
        正常结束（end）或出错（error 帧/WS 断开）时返回/抛异常。
        """
        from websockets.sync.client import connect

        assert self._base and self._cookie
        ws_url = self._base.replace("http://", "ws://") + "/api/remote.mux"
        stream_id = uuid.uuid4().hex
        with connect(
            ws_url,
            additional_headers={"Cookie": self._cookie, "Origin": self._base},
            open_timeout=15,
        ) as ws:
            self._follow_ws = ws
            try:
                ws.send(json.dumps({
                    "type": "open",
                    "streamId": stream_id,
                    "endpoint": "session/follow",
                    "payload": {"args": {"request": {
                        "address": {"kind": "session", "sessionId": session_id},
                        "maxMessages": 6,
                        "assistantStream": True,
                    }}},
                }, ensure_ascii=False))
                while True:
                    raw = ws.recv()
                    frame = json.loads(raw)
                    if frame.get("streamId") != stream_id:
                        continue
                    ftype = frame.get("type")
                    if ftype == "item":
                        if on_frame(frame.get("value")) is False:
                            ws.send(json.dumps({"type": "cancel", "streamId": stream_id}))
                            return
                    elif ftype == "end":
                        return
                    elif ftype == "error":
                        err = frame.get("error") or {}
                        raise DshError(err.get("code", "?"), err.get("message", ""))
            finally:
                self._follow_ws = None
