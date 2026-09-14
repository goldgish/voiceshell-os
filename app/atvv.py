"""ATVV 协议客户端（连接层核心）。

精简自 miremote-vibe/miremote/voice.py（MIT 协议，原作者 qi-o）。
负责：
- BLE GATT 直连小米遥控器 RC001-MS / RC003
- ATVV 协议握手（GET_CAPS）
- 订阅控制通道 + 音频通道
- 解码 IMA ADPCM 16kHz 音频帧
- 把协议事件通过回调送出（不在此处做转写/UI）

协议依据 miremote-vibe 项目真机实测（固件 2671，同款）：
- 服务 AB5E0001-5A21-4F05-BC7D-AF01F617B664
- AB5E0002 = TX（主机写命令），AB5E0003 = 音频 notify，AB5E0004 = 控制 notify
- GET_CAPS `0A 01 00 00 03 03`；MIC_OPEN `0C 00`，MIC_CLOSE `0D <stream>`
- 音频 IMA ADPCM 16kHz、120 字节帧，高 nibble 先行
- 按下语音键，遥控器自动发 0x04 STREAM_START + 音频帧
- 松手，遥控器自动发 0x00 STREAM_STOP
"""

from __future__ import annotations

import asyncio
import struct
import threading
import time
import winreg
from pathlib import Path


# ---- UUID / opcode 常量 ----
UUID_SVC = "ab5e0001-5a21-4f05-bc7d-af01f617b664"
UUID_TX = "ab5e0002-5a21-4f05-bc7d-af01f617b664"
UUID_RX_AUDIO = "ab5e0003-5a21-4f05-bc7d-af01f617b664"
UUID_CTRL = "ab5e0004-5a21-4f05-bc7d-af01f617b664"

OP_GET_CAPS = 0x0A
OP_CAPS_RESP = 0x0B
OP_MIC_OPEN = 0x0C
OP_MIC_CLOSE = 0x0D
OP_START_SEARCH_V1 = 0x08
OP_STREAM_START = 0x04
OP_STREAM_STOP = 0x00
OP_AUDIO_SYNC_V1 = 0x0A
OP_START_SEARCH = 0x10
OP_AUDIO_START = 0x11
OP_AUDIO_SYNC = 0x12
OP_AUDIO_STOP = 0x13

OPCODE_NAMES = {
    OP_CAPS_RESP: "CAPS_RESP", OP_MIC_OPEN: "MIC_OPEN", OP_MIC_CLOSE: "MIC_CLOSE",
    OP_START_SEARCH_V1: "START_SEARCH", OP_STREAM_START: "STREAM_START",
    OP_STREAM_STOP: "STREAM_STOP",
    OP_AUDIO_SYNC_V1: "AUDIO_SYNC",
    OP_START_SEARCH: "START_SEARCH", OP_AUDIO_START: "AUDIO_START",
    OP_AUDIO_SYNC: "AUDIO_SYNC", OP_AUDIO_STOP: "AUDIO_STOP",
}


# ---- BLE 设备发现（注册表枚举已配对设备，抄自 blediscover.py） ----
_BTHLE_KEY = r"SYSTEM\CurrentControlSet\Enum\BTHLEDevice"
_HW_TOKEN = "dev_vid&012717_pid&32b8"


def find_remote_addr() -> int | None:
    """返回已配对小米遥控器的蓝牙地址（48 位整数），找不到返回 None。"""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _BTHLE_KEY) as root:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                if _HW_TOKEN in name.casefold():
                    tail = name.rsplit("_", 1)[-1]
                    try:
                        addr = int(tail, 16)
                        if addr:
                            return addr
                    except ValueError:
                        continue
    except OSError:
        return None
    return None


# ---- winrt IBuffer 工具 ----
def ibuffer_bytes(buf) -> bytes:
    try:
        return bytes(buf)
    except Exception:
        pass
    from winrt.windows.storage.streams import DataReader
    dr = DataReader.from_buffer(buf)
    out = bytearray(dr.unconsumed_buffer_length)
    for i in range(len(out)):
        out[i] = dr.read_byte()
    return bytes(out)


def to_ibuffer(data: bytes):
    """bytes -> winrt IBuffer。"""
    from winrt.windows.storage.streams import DataWriter
    w = DataWriter()
    w.write_bytes(data)
    return w.detach_buffer()


# ---- IMA ADPCM 解码（16 kHz, 高 nibble 先行） ----
STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
]
INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


class ImaAdpcmDecoder:
    def __init__(self, predictor: int = 0, step_index: int = 0):
        self.predictor = predictor
        self.step_index = step_index

    def decode_nibble(self, nibble: int) -> int:
        step = STEP_TABLE[self.step_index]
        diff = step >> 3
        if nibble & 1:
            diff += step >> 2
        if nibble & 2:
            diff += step >> 1
        if nibble & 4:
            diff += step
        self.predictor += -diff if nibble & 8 else diff
        self.predictor = max(-32768, min(32767, self.predictor))
        self.step_index = max(0, min(88, self.step_index + INDEX_TABLE[nibble]))
        return self.predictor

    def decode(self, data: bytes) -> list[int]:
        pcm = []
        for b in data:
            pcm.append(self.decode_nibble(b >> 4))       # 高 nibble 先
            pcm.append(self.decode_nibble(b & 0x0F))
        return pcm


def write_wav(path: Path, pcm: list[int], rate: int = 16000):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))


class FrameAccumulator:
    """把任意长度的字节流按 frame_size 切成完整帧。"""

    def __init__(self, frame_size: int = 120):
        self.frame_size = max(1, int(frame_size or 120))
        self.pending = bytearray()

    def append(self, data: bytes) -> list[bytes]:
        self.pending.extend(data)
        frames = []
        while len(self.pending) >= self.frame_size:
            frames.append(bytes(self.pending[:self.frame_size]))
            del self.pending[:self.frame_size]
        return frames

    def reset(self):
        self.pending.clear()


# ---- AtvvClient ----
class AtvvClient:
    """ATVV 协议客户端。

    事件通过以下回调送出（在 winrt 线程触发，调用方负责线程安全）：
        on_stream_start(data: bytes)
        on_stream_stop(data: bytes)
        on_codec_sync(predictor: int, step_index: int)
        on_audio_frame(raw_adpcm: bytes)            # 每个 notify 包
        on_ctrl(data: bytes)                        # 任意控制通道通知
        on_caps(caps: dict)                         # 握手完成
    """

    def __init__(self, addr: int | None = None, log=print):
        self.addr = addr
        self.log = log
        self.dev = None
        self.tx = None
        self.audio_ch = None
        self.ctrl_ch = None
        self.audio_frames: list[bytes] = []
        self.audio_items: list[object] = []
        self.caps_resp: bytes | None = None
        self.audio_stopped = asyncio.Event()
        self.audio_started = asyncio.Event()
        self.caps_got = asyncio.Event()
        self._tokens = []
        self._decoder = ImaAdpcmDecoder()
        self._audio_subscribed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mic_open_future = None
        self._mic_open_requested = False
        self._capture_lock = threading.Lock()
        self.stream_id = 0x00
        self.stream_active = False
        self.stream_reason: int | None = None
        self.stop_reason: int | None = None
        self.protocol_version = 0x0100
        self.frame_size = 120
        self.selected_codec = 0x02
        self.interaction_model = 0
        # 外部回调
        self.on_audio_frame = None
        self.on_ctrl = None
        self.on_stream_start = None
        self.on_stream_stop = None
        self.on_codec_sync = None
        self.on_caps = None

    async def connect(self):
        """连接 BLE 设备，发现 ATVV 服务，订阅控制通道。"""
        self._loop = asyncio.get_running_loop()
        if self.addr is None:
            self.addr = find_remote_addr()
            if not self.addr:
                raise RuntimeError("未发现已配对的小米遥控器（请先在系统蓝牙设置里完成配对）")
        from winrt.windows.devices.bluetooth import BluetoothLEDevice, BluetoothCacheMode
        self.dev = await BluetoothLEDevice.from_bluetooth_address_async(self.addr)
        if self.dev is None:
            raise RuntimeError("打不开 BLE 设备（蓝牙没连上？）")
        svc_res = await self.dev.get_gatt_services_with_cache_mode_async(
            BluetoothCacheMode.UNCACHED
        )
        svc = None
        for s in svc_res.services:
            if str(s.uuid).lower() == UUID_SVC:
                svc = s
                break
        if svc is None:
            raise RuntimeError("设备上没有 AB5E0001 ATVV 服务")
        cr = await svc.get_characteristics_with_cache_mode_async(
            BluetoothCacheMode.UNCACHED
        )
        for ch in cr.characteristics:
            cu = str(ch.uuid).lower()
            if cu == UUID_TX:
                self.tx = ch
            elif cu == UUID_RX_AUDIO:
                self.audio_ch = ch
            elif cu == UUID_CTRL:
                self.ctrl_ch = ch
        if not (self.tx and self.audio_ch and self.ctrl_ch):
            raise RuntimeError("ATVV 特征不全")

        # 订阅控制通道
        token = self.ctrl_ch.add_value_changed(self._on_ctrl)
        self._tokens.append((self.ctrl_ch, token))
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue,
        )
        st = await self.ctrl_ch.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
        )
        self.log(f"控制通道已订阅 (状态 {st})")

    async def write(self, data: bytes):
        st = await self.tx.write_value_async(to_ibuffer(data))
        return st

    def _on_ctrl(self, _sender, args):
        data = ibuffer_bytes(args.characteristic_value)
        op = data[0] if data else -1
        name = OPCODE_NAMES.get(op, f"UNKNOWN_0x{op:02X}")
        self.log(f"[控制] {name} {data.hex(' ')}")
        if self.on_ctrl:
            try:
                self.on_ctrl(data)
            except Exception:
                pass
        if op == OP_CAPS_RESP:
            self.caps_resp = data
            info = self.parse_caps(data)
            self.protocol_version = info.get("version", self.protocol_version)
            self.frame_size = info.get("frame_size", self.frame_size)
            self.selected_codec = info.get("selected_codec", self.selected_codec)
            self.interaction_model = info.get("interaction", self.interaction_model)
            self.caps_got.set()
            if self.on_caps:
                try:
                    self.on_caps(info)
                except Exception:
                    pass
        elif op == OP_AUDIO_SYNC_V1 and len(data) >= 7:
            # v1.0 控制通道的 0x0A 是 AUDIO_SYNC，重置解码器
            predictor = struct.unpack(">h", data[4:6])[0]
            step_index = data[6]
            with self._capture_lock:
                self.audio_items.append(("sync", predictor, step_index))
            if self.on_codec_sync:
                try:
                    self.on_codec_sync(predictor, step_index)
                except Exception:
                    pass
        elif op == OP_AUDIO_START:
            if len(data) >= 4:
                self.stream_id = data[3]
            self.stream_active = True
            self.audio_started.set()
        elif op == OP_AUDIO_STOP:
            self.stream_active = False
            self.audio_stopped.set()
        elif op == OP_STREAM_STOP:
            # 松手时遥控器发 `00 02`，MIC_CLOSE 后回 `00 00`
            was_active = self.stream_active
            self.stream_active = False
            self.stop_reason = data[1] if len(data) >= 2 else None
            if was_active:
                self.audio_stopped.set()
            self._mic_open_requested = False
            if self.on_stream_stop:
                try:
                    self.on_stream_stop(data)
                except Exception:
                    pass
        elif op == OP_STREAM_START:
            # 0x04 是协议级新会话边界；在任何音频包到达前清掉上一流残留
            self.reset_capture()
            self.stream_reason = data[1] if len(data) >= 2 else None
            self.selected_codec = data[2] if len(data) >= 3 else self.selected_codec
            if len(data) >= 4:
                self.stream_id = data[3]
            self.stream_active = True
            self.stop_reason = None
            self.audio_stopped.clear()
            self.audio_started.set()
            if self.on_stream_start:
                try:
                    self.on_stream_start(data)
                except Exception:
                    pass
        elif op in (OP_START_SEARCH_V1, OP_START_SEARCH):
            self._schedule_mic_open_response()

    def _schedule_mic_open_response(self):
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if self._mic_open_requested:
            return
        pending = self._mic_open_future
        if pending is not None and not pending.done():
            return
        self._mic_open_requested = True
        future = asyncio.run_coroutine_threadsafe(
            self._respond_to_mic_open_request(), loop
        )
        self._mic_open_future = future
        future.add_done_callback(self._mic_open_done)

    async def _respond_to_mic_open_request(self):
        await self.write(bytes([OP_MIC_OPEN, 0x00]))

    def _mic_open_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self._mic_open_requested = False
            self.log(f"响应 MIC_OPEN 请求失败: {exc}")

    def _on_audio(self, _sender, args):
        data = ibuffer_bytes(args.characteristic_value)
        with self._capture_lock:
            self.audio_frames.append(data)
            self.audio_items.append(("audio", data))
        if self.on_audio_frame:
            try:
                self.on_audio_frame(data)
            except Exception:
                pass

    async def subscribe_audio(self):
        if self._audio_subscribed:
            return
        self._audio_subscribed = True
        token = self.audio_ch.add_value_changed(self._on_audio)
        self._tokens.append((self.audio_ch, token))
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue,
        )
        st = await self.audio_ch.write_client_characteristic_configuration_descriptor_async(
            GattClientCharacteristicConfigurationDescriptorValue.NOTIFY
        )
        self.log(f"音频通道已订阅 (状态 {st})")

    async def mic_open(self):
        """显式开启主机录音流（仅供诊断/手动录音，物理按键流不需要）。"""
        await self.subscribe_audio()
        with self._capture_lock:
            self.audio_frames.clear()
            self.audio_items.clear()
        self.audio_stopped.clear()
        self.audio_started.clear()
        await self.write(bytes([OP_MIC_OPEN, 0x00]))

    async def mic_close(self):
        await self.write(bytes([OP_MIC_CLOSE, self.stream_id & 0xFF]))

    async def resubscribe_audio(self):
        """重订阅音频通知（固件 2671 在 MIC_CLOSE 后订阅会失效）。"""
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattClientCharacteristicConfigurationDescriptorValue as CCCD,
        )
        for ch, token in list(self._tokens):
            if ch is self.audio_ch:
                try:
                    ch.remove_value_changed(token)
                except Exception:
                    pass
                self._tokens.remove((ch, token))
                break
        try:
            none_v = getattr(CCCD, "NONE", None)
            if none_v is not None:
                await self.audio_ch.write_client_characteristic_configuration_descriptor_async(none_v)
        except Exception:
            pass
        await asyncio.sleep(0.18)
        token = self.audio_ch.add_value_changed(self._on_audio)
        self._tokens.append((self.audio_ch, token))
        st = await self.audio_ch.write_client_characteristic_configuration_descriptor_async(
            CCCD.NOTIFY
        )
        self.log(f"音频通知已重订阅 (状态 {st})")

    def reset_capture(self):
        with self._capture_lock:
            self.audio_frames.clear()
            self.audio_items.clear()

    def drain_frames(self) -> list[bytes]:
        with self._capture_lock:
            frames, self.audio_frames = self.audio_frames, []
            self.audio_items = []
        return frames

    def drain_audio_items(self) -> list[object]:
        with self._capture_lock:
            items, self.audio_items = self.audio_items, []
            self.audio_frames = []
        return items

    @staticmethod
    def decode_audio_items(items: list[object], frame_size: int = 120) -> tuple[list[int], dict]:
        """按 ATVV 帧边界与 AUDIO_SYNC 顺序解码一段捕获。"""
        pcm: list[int] = []
        dec = ImaAdpcmDecoder()
        accumulator = FrameAccumulator(frame_size)
        for item in items:
            if isinstance(item, tuple) and item and item[0] == "sync":
                accumulator.reset()
                dec = ImaAdpcmDecoder(predictor=int(item[1]), step_index=int(item[2]))
                continue
            f = item[1] if isinstance(item, tuple) and item and item[0] == "audio" else item
            if not isinstance(f, (bytes, bytearray)) or not f:
                continue
            f = bytes(f)
            # v0.4 / 兼容固件：6B 头（大端 seq + padding + predictor + step）
            if len(f) in (frame_size + 6, 134):
                accumulator.reset()
                dec = ImaAdpcmDecoder(
                    predictor=struct.unpack(">h", f[3:5])[0], step_index=f[5]
                )
                pcm.extend(dec.decode(f[6:]))
                continue
            for frame in accumulator.append(f):
                pcm.extend(dec.decode(frame))
        return pcm, {}

    @staticmethod
    def frames_to_pcm(frames: list[bytes], frame_size: int = 120) -> list[int]:
        pcm, _stats = AtvvClient.decode_audio_items(frames, frame_size=frame_size)
        return pcm

    async def get_caps(self, timeout: float = 5.0) -> bytes | None:
        await self.write(bytes([OP_GET_CAPS, 0x01, 0x00, 0x00, 0x03, 0x03]))
        try:
            await asyncio.wait_for(self.caps_got.wait(), timeout)
        except asyncio.TimeoutError:
            self.log("GET_CAPS 超时无响应")
        return self.caps_resp

    @staticmethod
    def parse_caps(resp: bytes) -> dict:
        info = {"raw": resp.hex(" "), "length": len(resp)}
        if len(resp) >= 7:
            version = (resp[1] << 8) | resp[2]
            info["version"] = version
            info["version_byte"] = f"0x{version:04X}"
            codecs_std = resp[3]
            interaction = resp[4]
            if (codecs_std & 0x0F) == 0 and (resp[4] & 0x0F) != 0:
                codecs_std = resp[4]
                interaction = 0x03
                info["quirk"] = "codec 字节对调兼容"
            frame_size = (resp[5] << 8) | resp[6]
            info["adpcm_16k"] = bool(codecs_std & 0x02)
            info["adpcm_8k"] = bool(codecs_std & 0x01)
            info["selected_codec"] = 0x02 if codecs_std & 0x02 else 0x01
            info["interaction"] = interaction
            info["frame_size"] = frame_size or 120
            info["max_frame_size"] = frame_size or 120
        return info

    async def close(self):
        for ch, token in self._tokens:
            try:
                ch.remove_value_changed(token)
            except Exception:
                pass
        self._tokens.clear()
