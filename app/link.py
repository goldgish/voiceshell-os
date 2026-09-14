"""连接层进程：维持 ATVV BLE 连接，把协议事件通过 stdout JSON-line 送出。

设计要点：
- 纯 ATVV 协议层，不碰 rawinput、不碰 UI、不做转写
- 常驻订阅 ctrl + audio 通道；按下语音键，遥控器自动发 0x04 STREAM_START + 音频帧
- 松手时遥控器自动发 0x00 STREAM_STOP；连接层解码整段 PCM 写 WAV，路径回传给应用层
- stdin 接收 stop / subscribe_frames 命令；stdin EOF（父进程退出）自然结束

stdout JSON-line 协议（每行一个 JSON 对象）：
  连接层 -> 应用层:
    {"event":"log","msg":"..","level":"info"}
    {"event":"ready","caps":{...}}
    {"event":"stream_start","reason":..,"codec":..,"stream_id":..}
    {"event":"codec_sync","predictor":..,"step":..}
    {"event":"frame","seq":N,"adpcm_hex":"..","pcm_int16":[..]}   # 实时帧（含解码 PCM）
    {"event":"stream_stop","reason":..}
    {"event":"session_pcm","wav":"<绝对路径>","samples":N,"rate":16000}
    {"event":"disconnected","reason":".."}
  应用层 -> 连接层 (stdin):
    {"cmd":"stop"}
    {"cmd":"subscribe_frames","enabled":true}
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# 允许 `python link.py` 直接运行（把当前目录加进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from atvv import AtvvClient, ImaAdpcmDecoder, write_wav  # noqa: E402


# ---- 全局状态 ----
_emit_lock = threading.Lock()
_frame_seq = 0
_decoder = ImaAdpcmDecoder()         # 流式解码器（实时帧用）
_subscribe_frames = True
_stop_event = threading.Event()
_cli: AtvvClient | None = None
_loop: asyncio.AbstractEventLoop | None = None
_session_counter = 0


def emit(obj: dict) -> None:
    """线程安全地把 JSON 写到 stdout（单行）。"""
    line = json.dumps(obj, ensure_ascii=False)
    with _emit_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def log(msg: str, level: str = "info") -> None:
    emit({"event": "log", "msg": str(msg), "level": level})


# ---- ATVV 事件回调（winrt 线程触发） ----
def on_caps(caps: dict) -> None:
    emit({"event": "ready", "caps": caps})


def on_stream_start(data: bytes) -> None:
    global _decoder, _frame_seq, _streaming
    _streaming = True
    _decoder = ImaAdpcmDecoder()  # 新会话重置流式解码器
    _frame_seq = 0
    reason = data[1] if len(data) > 1 else None
    codec = data[2] if len(data) > 2 else None
    stream_id = data[3] if len(data) > 3 else None
    emit({
        "event": "stream_start",
        "reason": reason,
        "codec": codec,
        "stream_id": stream_id,
    })


def on_stream_stop(data: bytes) -> None:
    global _session_counter
    reason = data[1] if len(data) > 1 else None
    emit({"event": "stream_stop", "reason": reason})
    # 整段重新解码（保证 sync 重置正确），写 WAV
    if _cli is None:
        return
    items = _cli.drain_audio_items()
    pcm, _ = AtvvClient.decode_audio_items(items, _cli.frame_size)
    if not pcm:
        log("会话结束但无 PCM")
        return
    _session_counter += 1
    wav_name = f"voice_sess_{int(time.time())}_{_session_counter}.wav"
    wav_path = Path(tempfile.gettempdir()) / "miremote_voice" / wav_name
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_wav(wav_path, pcm, rate=16000)
        emit({
            "event": "session_pcm",
            "wav": str(wav_path),
            "samples": len(pcm),
            "rate": 16000,
            "duration_ms": int(len(pcm) / 16000 * 1000),
        })
    except Exception as exc:
        log(f"写 WAV 失败: {exc}", "error")


def on_codec_sync(predictor: int, step_index: int) -> None:
    global _decoder
    _decoder = ImaAdpcmDecoder(predictor=predictor, step_index=step_index)
    emit({"event": "codec_sync", "predictor": predictor, "step": step_index})


def on_audio_frame(raw: bytes) -> None:
    global _frame_seq
    if not _subscribe_frames:
        return
    pcm = _decoder.decode(raw)  # 流式解码（与 session_pcm 的整段重解码独立）
    _frame_seq += 1
    emit({
        "event": "frame",
        "seq": _frame_seq,
        "adpcm_hex": raw.hex(),
        # 120B 帧 = 240 个 int16 样本，对实时波形足够
        "pcm_int16": pcm,
    })


def on_ctrl(data: bytes) -> None:
    # 控制通道任意通知都送出（应用层可观察协议活动）
    emit({
        "event": "ctrl",
        "op": data[0] if data else None,
        "hex": data.hex(),
    })


# ---- stdin 命令读取 ----
def _stdin_reader() -> None:
    global _subscribe_frames
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = cmd.get("cmd")
        if action == "stop":
            log("收到 stop 命令")
            _stop_event.set()
            if _loop is not None:
                asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
            return
        elif action == "subscribe_frames":
            _subscribe_frames = bool(cmd.get("enabled", True))
            log(f"subscribe_frames = {_subscribe_frames}")


async def _shutdown() -> None:
    if _cli is not None:
        try:
            await _cli.close()
        except Exception:
            pass
    if _cli is not None and _cli.dev is not None:
        try:
            _cli.dev.close()
        except Exception:
            pass


# ---- 主循环：连接 + 自动重连 ----
async def main() -> None:
    global _cli, _loop
    _loop = asyncio.get_running_loop()
    threading.Thread(target=_stdin_reader, name="stdin-reader", daemon=True).start()

    log(f"连接层启动 (PID={os.getpid()})")
    while not _stop_event.is_set():
        _cli = AtvvClient(log=log)
        _cli.on_caps = on_caps
        _cli.on_stream_start = on_stream_start
        _cli.on_stream_stop = on_stream_stop
        _cli.on_codec_sync = on_codec_sync
        _cli.on_audio_frame = on_audio_frame
        _cli.on_ctrl = on_ctrl
        try:
            await _cli.connect()
            resp = await _cli.get_caps()
            if not resp:
                raise RuntimeError("GET_CAPS 无响应")
            await _cli.subscribe_audio()
            log("ATVV 连接就绪，等待语音键按下…")
            while not _stop_event.is_set():
                await asyncio.sleep(0.5)
        except Exception as exc:
            if _stop_event.is_set():
                break
            log(f"连接出错 ({type(exc).__name__}: {exc})", "error")
            emit({"event": "disconnected", "reason": f"{type(exc).__name__}: {exc}"})
            try:
                await _cli.close()
                if _cli.dev is not None:
                    _cli.dev.close()
            except Exception:
                pass
            await asyncio.sleep(2)
    log("连接层退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _stop_event.set()
