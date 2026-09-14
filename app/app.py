# -*- coding: utf-8 -*-
"""语音输入 · Windows 主程序（纯语音交互版）。

链路：
  遥控器语音键 → link.py(BLE/ATVV) → 录音 WAV → GLM-ASR 转写
  → POST 到 Web 实例的 /voice/input（秘书住在那儿，由它转达给某个对话）
  → pywebview(WebView2) 加载 overlay.html 对话面板展示
不做任何贴入/回车：浮窗本身就是对话界面。
本进程不再自建 dsh：只做「听」和「说」——转写文本投给秘书，播报由对话方经
MCP speak 工具回调本进程的 TTS 出声。

运行：python app.py   （或 pythonw app.py 无控制台）
"""
from __future__ import annotations

import atexit
import ctypes
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

import webview

sys.path.insert(0, str(Path(__file__).resolve().parent))
import landing  # noqa: E402
import tts  # noqa: E402
import voice_fallback  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
LINK = APP_DIR / "link.py"
OVERLAY_HTML = APP_DIR / "overlay.html"
FRAME_LOG = APP_DIR / "dsh_frames.log"   # P0 探测：follow 帧样本收集
ASSISTANT_LOG = APP_DIR / "assistant_frames.log"  # 排障：assistant-stream 原始帧（定位 text-delta 形状）
DEBUG_LOG = APP_DIR / "app_debug.log"    # 排障日志（pythonw 无控制台，关键事件落盘）

# Web 实例端口：秘书会话与插件住在那儿，语音文本投给它的 /voice/input。
HOST_PORT = os.environ.get("VOICE_HOST_PORT", "4177")


def _dbg(msg: str) -> None:
    """关键事件落盘，pythonw 下排查用。"""
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _host_url(path: str) -> str:
    return f"http://127.0.0.1:{HOST_PORT}{path}"


def _post_json(path: str, payload: dict, timeout: float = 15) -> dict:
    """投给 Web 实例里插件的路由；失败直接抛，由调用方决定说什么。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _host_url(path), data=body,
        headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def _get_json(path: str, timeout: float = 3) -> dict:
    with urllib.request.urlopen(_host_url(path), timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


_INSTANCE_MUTEX = None


def _acquire_single_instance() -> bool:
    """单实例守卫：第二个 app 实例直接退出（多开会抢 BLE 连接和会话写句柄）。"""
    global _INSTANCE_MUTEX
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, True, "Local\\MiRemoteVoiceCodingApp")
        if not h:
            return True  # 拿不到就放行，别挡住启动
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _INSTANCE_MUTEX = h  # 持有到进程退出
        return True
    except Exception:
        return True


# 语音文本改投 Web 实例的秘书（/voice/input），本地不再起 dsh；
# 原 dsh_sdk_bridge 与语音指令前缀（voice_prefix.py）留在原处，暂不参与本进程运行。


def _extract_summary(text: str) -> str:
    """从完整回复提取结果汇报段（约定在最后一段），清洗 Markdown 供 TTS 播报。

    规则解析（P0 从简，LLM 压缩留作 P2 可选项）：取最后一段 → 去 Markdown
    符号/列表标记/链接 → 超 150 字在句末标点处截断。
    """
    import re
    # 先去掉 Markdown 标题行（避免"结果"这类标题混入播报）
    text = re.sub(r"^\s*#{1,6}\s.*$", "", text, flags=re.M)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        return ""
    s = paras[-1]
    s = re.sub(r"[#*`>]", "", s)
    s = re.sub(r"^\s*(?:[-+]|\d+\.)\s+", "", s, flags=re.M)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > 150:
        cut = max(s.rfind(p, 0, 150) for p in "。！？!?")
        s = s[:cut + 1] if cut > 20 else s[:150] + "。"
    return s

# ---- Win32（仅用于浮窗：不抢焦点/不进任务栏/定位）----
_U32 = ctypes.windll.user32

_U32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_U32.GetWindowLongPtrW.restype = ctypes.c_longlong
_U32.SetWindowLongPtrW.restype = ctypes.c_longlong
_U32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
_U32.SetWindowRgn.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]
_U32.GetDpiForWindow.argtypes = [wintypes.HWND]
_U32.GetDpiForWindow.restype = wintypes.UINT
_U32.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_void_p,
                              ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint]

SW_HIDE = 0
SW_SHOWNA = 8          # 显示但不激活（不抢焦点）
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SPI_GETWORKAREA = 0x0030


# ---- API key ----
def _load_api_key() -> str | None:
    key = os.environ.get("ZHIPU_API_KEY")
    if key:
        return key
    env = APP_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ZHIPU_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


# ---- 开机自启（注册表 HKCU\...\Run）----
_AUTORUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTORUN_NAME = "VoiceInput"


def _autostart_cmd() -> str:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else sys.executable
    return f'"{exe}" "{APP_DIR / "app.py"}"'


def _is_autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _AUTORUN_NAME)
            return bool(val)
    except OSError:
        return False


def _set_autostart(enable: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, _AUTORUN_NAME, 0, winreg.REG_SZ, _autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, _AUTORUN_NAME)
            except FileNotFoundError:
                pass


# ---- H5 浮窗 ----
class Overlay:
    """pywebview(WebView2) 无边框置顶透明窗，加载 overlay.html 对话面板。

    - WS_EX_NOACTIVATE + SW_SHOWNA：显示但不抢焦点
    - Python → JS 单向事件：evaluate_js 调 window.onVoiceEvent
    - 页面未加载完时事件排队，loaded 后回放
    """

    WIDTH, HEIGHT = 480, 215
    TITLE = "语音对话浮窗"
    MARGIN = 24  # 距工作区上/右边缘的留白

    def __init__(self) -> None:
        x, y = self._top_right()
        self._win = webview.create_window(
            self.TITLE, url=str(OVERLAY_HTML),
            width=self.WIDTH, height=self.HEIGHT, x=x, y=y,
            frameless=True, on_top=True, hidden=True,
            # 不用 transparent=True：pywebview 会在导航时强制 Show+Activate，
            # 且 hidden 优先级更高导致透明 hack 不生效 → 白框。
            # 圆角改由 setup_native 的 SetWindowRgn 实现。
            background_color="#0d101b",
        )
        self._hwnd: int = 0
        self._ready = False
        self._pending: list[str] = []
        self._win.events.loaded += self._on_loaded

    @staticmethod
    def _top_right() -> tuple[int, int]:
        """浮在屏幕右上角（按工作区算，避开任务栏）。"""
        rc = wintypes.RECT()
        if _U32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0):
            x = rc.right - Overlay.WIDTH - Overlay.MARGIN
            y = rc.top + Overlay.MARGIN
            return x, y
        return 1200, 24  # 兜底

    def _on_loaded(self) -> None:
        self._ready = True
        for js in self._pending:
            self._win.evaluate_js(js)
        self._pending.clear()

    def setup_native(self) -> None:
        """webview.start() 之后调用：拿 hwnd，设 NOACTIVATE/TOOLWINDOW 样式 + 圆角。"""
        deadline = time.time() + 5
        while time.time() < deadline and not self._hwnd:
            self._hwnd = self._find_hwnd()
            if not self._hwnd:
                time.sleep(0.1)
        if not self._hwnd:
            print("[overlay] 未找到浮窗 hwnd", flush=True)
            return
        ex = _U32.GetWindowLongPtrW(self._hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        _U32.SetWindowLongPtrW(self._hwnd, GWL_EXSTYLE, ex)
        self._reposition()
        self._apply_rounded_region()
        print(f"[overlay] hwnd={self._hwnd:#x}", flush=True)

    def _reposition(self) -> None:
        """按实测窗口大小重新贴到右上角。

        150% DPI 下 WinForms 会把窗口实际放大，实测尺寸跟请求值对不上
        （实测宽 465 ≠ 480），create_window 传的 x,y 会偏出屏幕，
        这里用 GetWindowRect 实测值修正。
        """
        rc = wintypes.RECT()
        wa = wintypes.RECT()
        if not (_U32.GetWindowRect(self._hwnd, ctypes.byref(rc))
                and _U32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)):
            return
        w = rc.right - rc.left
        x = wa.right - w - self.MARGIN
        y = wa.top + self.MARGIN
        _U32.SetWindowPos(self._hwnd, ctypes.c_void_p(HWND_TOPMOST),
                          x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def _apply_rounded_region(self) -> None:
        """用 SetWindowRgn 裁出圆角（替代 pywebview transparent）。"""
        rc = wintypes.RECT()
        if not _U32.GetWindowRect(self._hwnd, ctypes.byref(rc)):
            return
        w, h = rc.right - rc.left, rc.bottom - rc.top
        # 卡片 CSS 圆角 18px → 物理像素按窗口 DPI 换算
        dpi = _U32.GetDpiForWindow(self._hwnd) or 96
        r = max(1, round(18 * dpi / 96))
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        rgn = gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, 2 * r, 2 * r)
        if rgn:
            _U32.SetWindowRgn(self._hwnd, ctypes.c_void_p(rgn), True)

    def _find_hwnd(self) -> int:
        found: list[int] = []
        my_pid = os.getpid()
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd: int, lparam: int) -> bool:
            pid = wintypes.DWORD()
            _U32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid:
                buf = ctypes.create_unicode_buffer(64)
                _U32.GetWindowTextW(hwnd, buf, 64)
                # 页面加载后窗口标题会被 <title>语音对话</title> 覆盖，用前缀匹配
                if buf.value.startswith("语音对话"):
                    found.append(hwnd)
                    return False  # 找到即停
            return True

        _U32.EnumWindows(WNDENUMPROC(cb), 0)
        return found[0] if found else 0

    # 以下方法任意线程可调用（pywebview 内部封送到 GUI 线程）----
    def emit(self, ev: dict) -> None:
        js = f"window.onVoiceEvent && window.onVoiceEvent({json.dumps(ev, ensure_ascii=False)})"
        if self._ready:
            self._win.evaluate_js(js)
        else:
            self._pending.append(js)

    def show(self) -> None:
        if self._hwnd:
            _U32.ShowWindow(self._hwnd, SW_SHOWNA)
        else:
            self._win.show()

    def hide(self) -> None:
        if self._hwnd:
            _U32.ShowWindow(self._hwnd, SW_HIDE)
        else:
            self._win.hide()

    def destroy(self) -> None:
        self._win.destroy()


# ---- 托盘 ----
class Tray:
    def __init__(self, app: "VoiceApp") -> None:
        import pystray
        self._app = app
        self._icon = pystray.Icon(
            "voice-input", self._make_image(False), "语音对话",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "打开落地页",
                    lambda icon, item: app.open_landing(),
                ),
                pystray.MenuItem(
                    "开机自启",
                    self._toggle_autostart,
                    checked=lambda item: _is_autostart_enabled(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", lambda icon, item: app.quit()),
            ),
        )

    @staticmethod
    def _make_image(connected: bool):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = (52, 211, 153, 255) if connected else (150, 150, 150, 255)
        d.ellipse((10, 10, 54, 54), fill=color)
        return img

    def set_status(self, connected: bool) -> None:
        self._icon.icon = self._make_image(connected)
        self._icon.title = "语音对话（遥控器已连接）" if connected else "语音对话（遥控器连接中…）"

    def _toggle_autostart(self, icon, item) -> None:
        _set_autostart(not _is_autostart_enabled())

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()


# ---- 主应用 ----
class VoiceApp:
    def __init__(self, selftest: str | None = None) -> None:
        self._selftest = selftest
        self.overlay = Overlay()
        self.tray: Tray | None = None
        self._client = None          # GLM-ASR client（复用）
        self._proc: subprocess.Popen | None = None
        self._hide_timer: threading.Timer | None = None
        self._quitting = False
        self._turn_spoke = False     # 本回合对话方是否已用 speak 工具播报过
        self._turn_finish = None     # 本回合 turn/end 的 finish kind（熔断善后用）
        self._current_tool_title = ""  # 当前工具名（心跳硬兜底时告诉用户在忙什么）
        # TTS（P0）
        self.tts = tts.EdgeTts()
        self._reply_buf = ""         # 累积本回合回复全文
        # 落地页（浏览器常驻入口 + MCP speak 工具的回调端口）
        self.landing = landing.Landing(APP_DIR)
        self.landing.start()
        self.landing.set_speak_handler(self._on_voice_speak)
        try:
            (APP_DIR / "voice_http.port").write_text(
                str(self.landing.port), encoding="utf-8")
        except OSError:
            pass
        self.tts.warmup()
        # 长任务心跳：回合进行中 90s 无播报就本地硬兜底朗读
        # （原来 35s 那档是往 agent 收件箱塞提醒，随本地 dsh 一并去掉了）
        self._turn_active = False
        self._turn_hb_last_voice = 0.0  # 上次发声（对话方播报或硬兜底）时刻
        self._hb_fallbacks = 0          # 本回合已硬兜底几次（连续无回应就不再念）
        threading.Thread(target=self._hb_loop, name="tts-hb", daemon=True).start()

    # ---- 事件广播中心：浮窗（overlay）+ 落地页（SSE）同源 ----
    def _emit(self, ev: dict) -> None:
        self.overlay.emit(ev)
        self.landing.push(ev)

    # ---- link.py 子进程 ----
    def start_link(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-u", str(LINK)],
            cwd=str(APP_DIR), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        atexit.register(self._stop_link)
        threading.Thread(target=self._read_loop, name="link-reader", daemon=True).start()

    def _stop_link(self) -> None:
        if not self._proc:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
                self._proc.stdin.flush()
        except OSError:
            pass
        try:
            self._proc.terminate()
        except OSError:
            pass

    def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._on_event(ev)
        print("[app] link.py 已退出", flush=True)
        if not self._quitting and self.tray:
            self.tray.set_status(False)
        if not self._quitting:
            self.landing.set_status(remote=False)

    # ---- 遥控器事件分发 ----
    def _on_event(self, ev: dict) -> None:
        et = ev.get("event")
        if et == "ready":
            if self.tray:
                self.tray.set_status(True)
            self.landing.set_status(remote=True)
        elif et == "stream_start":
            self._on_stream_start()
        elif et == "frame":
            self._on_frame(ev.get("pcm_int16") or [])
        elif et == "stream_stop":
            self._emit({"type": "recognizing"})
        elif et == "session_pcm":
            dur = ev.get("duration_ms", 0)
            print(f"[app] 录音 {dur}ms → 转写", flush=True)
            self._start_transcribe(ev.get("wav", ""), dur)
        elif et == "log":
            print(f"[link] {ev.get('msg', '')}", flush=True)

    def _on_stream_start(self) -> None:
        _dbg("按下语音键，开始录音")
        if self._hide_timer:
            self._hide_timer.cancel()
            self._hide_timer = None
        self.tts.stop()              # 打断即应答：按下语音键立即停止播报
        self.overlay.show()
        self._emit({"type": "recording"})
        print("[app] 录音开始", flush=True)

    def _on_frame(self, pcm: list[int]) -> None:
        if not pcm:
            return
        # 只传 RMS 振幅给 H5（每帧一个 float，省序列化开销）
        rms = math.sqrt(sum(s * s for s in pcm) / len(pcm)) / 32768.0
        self._emit({"type": "frame", "amp": round(min(rms * 3.5, 1.0), 3)})

    # ---- 转写 ----
    def _start_transcribe(self, wav_path: str, dur_ms: int) -> None:
        def worker() -> None:
            ok, text = self._transcribe(wav_path, dur_ms)
            self._on_transcribe_done(ok, text)
        threading.Thread(target=worker, name="asr", daemon=True).start()

    def _transcribe(self, wav_path: str, dur_ms: int) -> tuple[bool, str]:
        if not wav_path or not Path(wav_path).exists():
            return False, f"录音 {dur_ms}ms（文件丢失）"
        if self._client is None:
            key = _load_api_key()
            if not key:
                return False, f"录音 {dur_ms}ms（未配置 ZHIPU_API_KEY）"
            try:
                from zai import ZhipuAiClient
                self._client = ZhipuAiClient(api_key=key)
            except Exception as exc:
                return False, f"SDK 初始化失败: {exc}"
        try:
            with open(wav_path, "rb") as f:
                # 非流式整体返回比流式快（省 SSE 开销）
                resp = self._client.audio.transcriptions.create(model="glm-asr", file=f)
                text = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
            text = (text or "").strip()
            if not text:
                return False, f"（空文本，录音 {dur_ms}ms）"
            return True, text
        except Exception as exc:
            return False, f"转写失败: {exc}"

    def _on_transcribe_done(self, ok: bool, text: str) -> None:
        """转写完成：H5 出用户气泡 → 投给 Web 实例的秘书。"""
        if not ok:
            print(f"[app] 转写失败: {text}", flush=True)
            self._emit({"type": "error", "text": text})
            self.tts.speak("没听清，请再说一次")
            self._schedule_hide(5.0)
            return
        print(f"[app] 转写: {text}", flush=True)
        _dbg(f"转写完成: {text[:30]}")
        self._emit({"type": "user_text", "text": text})
        self.landing.record("user", text)
        threading.Thread(target=self._send_prompt, args=(text,),
                         name="voice-input", daemon=True).start()

    def _schedule_hide(self, delay: float) -> None:
        """延时收浮窗。收窗只是收窗，不代表回合结束（回合由心跳按会话状态判定）。"""
        if self._hide_timer:
            self._hide_timer.cancel()
        self._hide_timer = threading.Timer(delay, self.overlay.hide)
        self._hide_timer.daemon = True
        self._hide_timer.start()

    # ---- 语音播报回调（对话方的 MCP speak 工具 → landing HTTP → 这里）----
    def _on_voice_speak(self, text: str) -> None:
        """播报即视作回合有进展：刷新心跳，出声。"""
        _dbg(f"speak 工具播报: {text[:50]}")
        print(f"[tts] speak 播报: {text[:50]}", flush=True)
        self._turn_active = True
        self._turn_hb_last_voice = time.time()

        def _done(t=text) -> None:   # 播完率埋点：验证"送达"真的"播完"
            _dbg(f"speak 播完: {t[:50]}")
            print(f"[tts] 播完: {t[:50]}", flush=True)
            self._emit({"type": "assistant_done"})
            self.landing.record("assistant", t)

        self.tts.speak(text, on_done=_done)

    def _host_busy(self) -> bool:
        """Web 实例里还有没有会话在跑——App 判断回合是否结束的唯一依据。"""
        return bool(_get_json("/voice/busy").get("busy"))

    def _hb_loop(self) -> None:
        """回合状态机 + 长任务心跳。

        App 只看得见"有没有在播报"，看不见会话事件，所以每 5 秒问一次 Web 实例：
        还有会话在跑吗。没在跑 + 也静默了 → 本回合结束，按产品规则 20 秒后收窗；
        还在跑 + 静默超过 90 秒 → 本地朗读兜底，免得用户把静默听成卡死。
        """
        while not self._quitting:
            time.sleep(5)
            try:
                if not self._turn_active:
                    continue
                now = time.time()
                silent = now - self._turn_hb_last_voice
                if self._host_busy():
                    if silent > 90:
                        self._turn_hb_last_voice = now
                        # M4：带上下文，别只说"还在忙"（文案与评测跑器同一来源）
                        msg = voice_fallback.hard_fallback_text(self._current_tool_title)
                        _dbg(f"心跳硬兜底：对话方 90 秒无播报 → 「{msg}」")
                        self.tts.speak(msg)
                    continue
                # 没有会话在跑了 = 这轮完事：留 20 秒给用户看，再收窗
                if silent > 20:
                    self._turn_active = False
                    _dbg("回合结束（已无运行中的会话）")
                    self._schedule_hide(20.0)
            except Exception:
                pass

    # ---- Web 实例（秘书住那儿）----
    def _probe_host(self) -> None:
        """探一下 Web 实例在不在，结果同步到落地页状态灯。"""
        try:
            _get_json("/voice/status")
            print("[voice] Web 实例在线，语音将投给秘书", flush=True)
            self.landing.set_status(dsh=True)
        except Exception as exc:
            print(f"[voice] Web 实例不可达: {exc}", flush=True)
            _dbg(f"Web 实例探测失败: {exc!r}")
            self.landing.set_status(dsh=False)

    # ---- 浮窗卡片投影层：本地 dsh 停用后暂时没有数据源 ----
    # 这层原本吃 SDK 的 session.event（思考/工具卡片）。改走秘书之后事件不再流经本进程，
    # 先原样留着：若将来由插件把会话事件转发过来，这里可以直接复用。
    def _on_sdk_event(self, etype: str, data: dict) -> None:
        """session.event 持久事件 → 浮窗卡片（回调线程，任何异常只记录不扩散）。"""
        try:
            self._on_sdk_event_inner(etype, data)
        except Exception as exc:
            _dbg(f"投影异常 {etype}: {exc!r}")
        # 样本落盘（沿用 dsh_frames.log），跳过高频噪音事件
        if etype not in ("agent/inbox/spliced", "system/message", "user/message",
                         "request/header", "request/context", "session/title"):
            try:
                with open(FRAME_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": etype, "data": data},
                                       ensure_ascii=False)[:800] + "\n")
            except OSError:
                pass

    def _on_sdk_event_inner(self, etype: str, data: dict) -> None:
        if etype == "assistant/message":
            self._project_assistant_message(data.get("message") or {})
        elif etype == "tool/call":
            cid = data.get("callId")
            name = data.get("name") or "tool"
            if "voice" in (name or ""):
                self._turn_spoke = True
                self._turn_hb_last_voice = time.time()   # 有播报，心跳重新计时
            if str(name).startswith("mcp__voice__"):
                args = self._tool_args(data.get("arguments")) or {}
                title, preview = "语音播报", self._preview(args.get("text") or "", 30)
            else:
                title, preview = self._tool_title(name, data.get("arguments"))
                self._current_tool_title = title   # M4：心跳硬兜底带上下文用
            self._emit({"type": "tool_call", "callId": cid, "name": name,
                        "title": title, "preview": preview})
            self._emit({"type": "status", "icon": "tool", "label": "调用工具"})
        elif etype == "tool/result":
            self._project_tool_result(data.get("message") or {})
        elif etype == "step/start":
            self._emit({"type": "status", "icon": "thinking", "label": "思考中"})
        elif etype == "turn/end":
            self._turn_finish = ((data.get("reason") or {}).get("kind"))

    def _on_sdk_status(self, status: str, payload: dict) -> None:
        """session.status：idle → 回合完成（历史落盘 + 兜底 TTS + 自动隐藏）。"""
        if status != "idle":
            return
        self._turn_active = False
        buf = self._reply_buf
        self._emit({"type": "assistant_done"})
        self.landing.record("assistant", buf)
        # 兜底：agent 全程没调 speak 工具时，app 播最终总结（保证语音回环不为空）
        if not self._turn_spoke:
            summary = _extract_summary(buf) or "任务完成"
            _dbg(f"兜底 TTS: {summary[:50]}")
            self.tts.speak(summary)
        _dbg(f"回合完成 buf={len(buf)}字 spoke={self._turn_spoke}")
        self._schedule_hide(20.0)  # 留时间看回复

    # ---- 投影层：SDK session.event → 浮窗卡片 ----
    # 数据源已从 ws 帧换成官方持久事件（session.event），前端事件协议不变。
    def _project_assistant_message(self, msg: dict) -> None:
        """assistant/message：content 含 reasoning 与 text 块。

        - reasoning 块：思考卡片（灰色斜体）。TTS 不再从这里取【汇报】，改由
          agent 主动调用 speak 工具（MCP）播报
        - text 块：回复文本 → 气泡段落级追加（SDK 无 chunk 级流式）
        - tool-call 块：跳过（随后必有 tool/call 事件，统一在事件里出卡片）
        """
        for block in (msg.get("content") or []):
            bt = block.get("type")
            if bt == "reasoning" and block.get("text"):
                self._emit({"type": "thinking", "text": self._preview(block["text"], 100)})
            elif bt == "text" and block.get("text"):
                self._reply_buf += block["text"]
                self._emit({"type": "assistant_chunk", "text": block["text"]})

    @staticmethod
    def _tool_args(args_raw) -> dict:
        args: dict = {}
        if args_raw:
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                args = {}
        return args if isinstance(args, dict) else {}

    def _project_tool_result(self, msg: dict) -> None:
        """tool/result：找 callId + isError，更新对应卡片为完成/失败。"""
        cid = None
        ok = True
        err_txt = ""
        for blk in (msg.get("content") or []):
            if blk.get("type") == "tool-result":
                cid = blk.get("toolCallId")
                if blk.get("isError"):
                    ok = False
                # 提取 errorSummary（参考 dsh-TUI ToolRowModel.errorSummary）
                if not ok:
                    for c in (blk.get("content") or []):
                        if isinstance(c, dict) and c.get("type") == "text":
                            err_txt = (c.get("text") or "").splitlines()[0] if c.get("text") else ""
                            break
                break
        if cid:
            self._emit({"type": "tool_result", "callId": cid, "ok": ok,
                        "error": err_txt[:80]})

    @staticmethod
    def _preview(text: str, limit: int = 60) -> str:
        """压成一行 + 省略号。参考 dsh-TUI transcript.ts preview()。"""
        s = re.sub(r"\s+", " ", (text or "").strip().replace("\n", " "))
        return s if len(s) <= limit else s[:limit - 1] + "…"

    @staticmethod
    def _tool_title(tool_name: str, args_raw) -> tuple[str, str]:
        """按工具 variant 提取中文标题 + 预览。

        参考 dsh-TUI tool-call-model.ts：classifyTool 分 variant，每个 variant 有
        本地化标题键 + args 字段提取摘要。我们简化为按工具名分派，标题中文、摘要
        从 args 取关键字段（write/edit 取 path、pwsh/bash 取 description 或命令首行、
        read_image/read 取 path/url）。TTS 不读工具卡片标题（汇报走 reasoning【汇报】），
        标题只是给浮窗瞄一眼。
        """
        # 先解析 args
        args: dict = {}
        if args_raw:
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except Exception:
                args = {}

        n = (tool_name or "").lower()
        # write/edit：文件路径
        if n in ("write", "edit", "str_replace_editor"):
            path = (args.get("path") or args.get("file_path") or "").strip()
            fname = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
            verb = "写入" if n == "write" else "编辑"
            return (f"{verb} {fname}" if fname else f"{verb}文件", path)
        # pwsh/bash：description > 命令首行
        if n in ("pwsh", "bash"):
            desc = (args.get("description") or "").strip()
            cmd = (args.get("command") or "").strip()
            cmd_first = cmd.splitlines()[0] if cmd else ""
            return (desc or cmd_first or "执行命令", cmd_first if desc else "")
        # read_image：path/url
        if n == "read_image":
            path = (args.get("path") or args.get("url") or "").strip()
            fname = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
            return (f"查看 {fname}" if fname else "查看图片", path)
        # read：path
        if n == "read":
            path = (args.get("path") or args.get("file_path") or "").strip()
            fname = path.replace("\\", "/").rsplit("/", 1)[-1] if path else ""
            return (f"读取 {fname}" if fname else "读取文件", path)
        # search/grep/glob
        if n in ("grep", "glob", "search"):
            pat = (args.get("pattern") or args.get("query") or args.get("path") or "").strip()
            return (f"搜索 {pat[:30]}" if pat else "搜索", pat)
        # present：交付
        if n == "present":
            return ("交付成果", "")
        # 兜底
        desc = (args.get("description") or "").strip()
        if desc:
            return (desc, "")
        return (tool_name or "工具调用", "")

    def _send_prompt(self, text: str) -> None:
        """转写文本投给秘书（POST /voice/input），由秘书转达给当前对话。

        投递是「收件即回」，不阻塞：往后的播报由对话方经 speak 工具回流本进程的 TTS，
        浮窗收合与心跳都跟着播报走。
        """
        self._reply_buf = ""
        self._turn_spoke = False
        self._turn_finish = None
        self._turn_active = True
        self._turn_hb_last_voice = time.time()
        self._hb_fallbacks = 0
        self._emit({"type": "assistant_start"})
        try:
            _post_json("/voice/input", {"text": text})
            print(f"[voice] 已投给秘书: {text[:40]}", flush=True)
        except Exception as exc:
            msg = str(exc)
            print(f"[voice] 投递失败: {msg}", flush=True)
            _dbg(f"投递秘书失败: {msg[:200]}")
            self._emit({"type": "error", "text": f"投递失败: {msg[:80]}"})
            self.tts.speak("连不上对话服务，看看 Web 实例起了没")
            self._schedule_hide(5.0)

    # ---- 生命周期 ----
    def open_landing(self) -> None:
        import webbrowser
        webbrowser.open(self.landing.url())

    def quit(self) -> None:
        self._quitting = True
        self.tts.stop()
        self.landing.stop()
        self._stop_link()
        if self.tray:
            self.tray.stop()
        self.overlay.destroy()

    def run(self) -> None:
        self.start_link()
        self.tray = Tray(self)
        threading.Thread(target=self.tray.run, name="tray", daemon=True).start()
        # WebView2 窗口样式（不抢焦点/不进任务栏）在 GUI 启动后设置
        threading.Thread(target=self.overlay.setup_native, name="overlay-setup", daemon=True).start()
        # 探一下 Web 实例（秘书住那儿）在不在
        threading.Thread(target=self._probe_host, name="host-probe", daemon=True).start()
        print("[app] 语音对话已启动（纯语音交互）", flush=True)
        # 自测通道：不出声按键也能端到端验证（speak 工具 → TTS 发声可见可听）
        if self._selftest:
            def _selftest_run() -> None:
                time.sleep(2)
                self._send_prompt(self._selftest)
            threading.Thread(target=_selftest_run, name="selftest", daemon=True).start()
        # 落地页是常驻入口：启动后自动在浏览器打开
        threading.Timer(1.2, self.open_landing).start()
        webview.start(gui="edgechromium", debug=False)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", default=None,
                    help="Agent 就绪后自动发送一条测试指令（端到端验证用）")
    args, _ = ap.parse_known_args()
    if not _acquire_single_instance():
        print("[app] 已有实例在运行，退出", flush=True)
        return
    # 每次启动清空排障日志，便于对应当次运行
    for p in (DEBUG_LOG, ASSISTANT_LOG):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _dbg("=== app 启动 ===")
    # 进程设为 Per-Monitor DPI 感知：保证 GetWindowRect/SetWindowPos/SetWindowRgn
    # 与 WebView2 渲染同为物理像素，否则 150% 缩放下坐标系混乱（圆角裁剪错位）。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    VoiceApp(selftest=args.selftest).run()


if __name__ == "__main__":
    main()
