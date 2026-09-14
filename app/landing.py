# -*- coding: utf-8 -*-
"""落地页服务：本地 HTTP + SSE，给浏览器提供常驻入口页面。

- GET /           落地页（landing.html）
- GET /events     SSE 事件流（与浮窗同源：recording/recognizing/user_text/assistant_*/error/amp/status）
- GET /api/state  初始状态（连接状态 + 最近 40 条历史）

历史持久化在 history.json（最多 40 条，与浮窗一致）。纯展示，不提供文字输入。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_HISTORY = 40


class Landing:
    def __init__(self, app_dir: Path, base_port: int = 8790) -> None:
        self._dir = Path(app_dir)
        self._history_file = self._dir / "history.json"
        self._history: list[dict] = self._load_history()
        self._status = {"remote": False, "dsh": False}
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._speak_handler = None   # app 注入：收到 MCP speak 请求 → tts.speak(text)
        self.port = base_port
        self._server: ThreadingHTTPServer | None = None

    # ---- 生命周期 ----
    def start(self) -> bool:
        for p in range(self.port, self.port + 10):
            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", p), self._make_handler())
                self.port = p
                break
            except OSError:
                continue
        if self._server is None:
            print("[landing] 没有可用端口，落地页未启动", flush=True)
            return False
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever,
                         name="landing", daemon=True).start()
        print(f"[landing] 落地页 {self.url()}", flush=True)
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # ---- 事件与状态 ----
    def push(self, ev: dict) -> None:
        """广播事件给所有打开的落地页。"""
        data = json.dumps(ev, ensure_ascii=False)
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                pass  # 慢客户端丢帧，不阻塞主流程

    def set_status(self, **kv: bool) -> None:
        self._status.update(kv)
        self.push({"type": "status", **self._status})

    def set_speak_handler(self, fn) -> None:
        """注入语音播报回调：MCP speak 工具经 /voice/speak 触发 app 内 TTS。"""
        self._speak_handler = fn

    def record(self, role: str, text: str) -> None:
        """记录一条已完成的消息（user/assistant）到历史并落盘。"""
        text = (text or "").strip()
        if not text:
            return
        self._history.append({"role": role, "text": text, "ts": time.time()})
        self._history = self._history[-MAX_HISTORY:]
        try:
            self._history_file.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass

    def _load_history(self) -> list[dict]:
        try:
            data = json.loads(self._history_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data[-MAX_HISTORY:]
        except (OSError, ValueError):
            pass
        return []

    # ---- HTTP ----
    def _make_handler(self):
        landing = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:  # 静音访问日志
                pass

            def _send(self, body: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/events"):
                    self._sse()
                elif self.path.startswith("/api/state"):
                    body = json.dumps({
                        "status": landing._status,
                        "history": landing._history,
                    }, ensure_ascii=False).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                else:
                    try:
                        body = (landing._dir / "landing.html").read_bytes()
                    except OSError:
                        body = b"landing.html missing"
                    self._send(body, "text/html; charset=utf-8")

            def do_POST(self) -> None:
                # MCP speak 工具入口：{text} → app 内 TTS 引擎播报（异步，立即返回）
                if self.path.startswith("/voice/speak"):
                    text = ""
                    try:
                        n = int(self.headers.get("Content-Length") or 0)
                        data = json.loads(self.rfile.read(n) or b"{}")
                        text = str(data.get("text") or "").strip()
                    except Exception:
                        text = ""
                    if text and landing._speak_handler:
                        threading.Thread(target=landing._speak_handler,
                                         args=(text,), daemon=True).start()
                        self._send(b'{"ok":true}', "application/json; charset=utf-8")
                    else:
                        self._send(b'{"ok":false}', "application/json; charset=utf-8")
                else:
                    self.send_error(404)

            def _sse(self) -> None:
                q: queue.Queue = queue.Queue(maxsize=500)
                with landing._lock:
                    landing._clients.append(q)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    self.wfile.write(b"retry: 2000\n\n")
                    self.wfile.flush()
                    while True:
                        try:
                            data = q.get(timeout=15)
                            payload = f"data: {data}\n\n".encode("utf-8")
                        except queue.Empty:
                            payload = b": ping\n\n"
                        self.wfile.write(payload)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    pass
                finally:
                    with landing._lock:
                        if q in landing._clients:
                            landing._clients.remove(q)

        return Handler
