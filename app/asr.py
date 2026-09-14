"""智谱 GLM-ASR 转写模块（从 app.py 提取，供 ws_server.py 复用）。

设计：
- async transcribe(wav, dur_ms) -> {"text": str, "ok": bool}，内部用 run_in_executor 跑同步 SDK
- client 缓存为模块级，省初始化开销（复用 app.py 的 self._client 思路）
- _load_api_key 先读环境变量再读 app/.env（复用 app.py L300-311）
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


_client = None  # 模块级缓存 ZhipuAiClient，省初始化开销


def _load_api_key() -> str:
    """读 ZHIPU_API_KEY：先环境变量，再 app/.env。"""
    key = os.environ.get("ZHIPU_API_KEY", "")
    if key:
        return key
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ZHIPU_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _transcribe_sync(wav_path: str, dur_ms: int) -> dict:
    """同步转写（在工作线程跑）。返回 {"text": str, "ok": bool}。"""
    global _client
    if not wav_path or not Path(wav_path).exists():
        return {"text": f"录音 {dur_ms}ms（文件丢失）", "ok": False}
    if _client is None:
        key = _load_api_key()
        if not key:
            return {"text": f"录音 {dur_ms}ms（未配置 ZHIPU_API_KEY）", "ok": False}
        try:
            from zai import ZhipuAiClient
            _client = ZhipuAiClient(api_key=key)
        except Exception as exc:
            return {"text": f"SDK 初始化失败: {exc}", "ok": False}
    try:
        with open(wav_path, "rb") as f:
            # 短音频用非流式，整体返回比流式更快（省 SSE 开销）
            resp = _client.audio.transcriptions.create(
                model="glm-asr", file=f
            )
            # 非流式返回 str 或对象，统一取 text
            text = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
        return {"text": text or f"（空文本，录音 {dur_ms}ms）", "ok": bool(text)}
    except Exception as exc:
        return {"text": f"转写失败: {exc}", "ok": False}


async def transcribe(wav_path: str, dur_ms: int = 0) -> dict:
    """异步转写。在默认 executor 跑同步 SDK 调用，不阻塞 asyncio 事件循环。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_sync, wav_path, dur_ms)
