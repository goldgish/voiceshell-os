# -*- coding: utf-8 -*-
"""TTS 播报抽象（Edge-TTS 引擎 + pygame 播放，句子级流水线，支持打断）。

设计：
- speak(text)：非阻塞。按句切分后流水线执行：合成第 N+1 句的同时播放第 N 句，
  首句合成完即出声（~1s），不再等整段合成（原来整段要 ~2.5s+ 才出声）
- stop()：立即停止播放；代次（generation）作废在途合成与待发队列
- 引擎可换：后续加 GLM-TTS/SAPI 只需实现同一 speak/stop 接口
- 任何失败只打日志不抛异常（播报是增强，不能拖垮主链路）
"""

from __future__ import annotations

import asyncio
import queue
import re
import tempfile
import threading
import time
from pathlib import Path

_TTS_DIR = Path(tempfile.gettempdir()) / "miremote_voice" / "tts"

_SENT_END = re.compile(r"(?<=[。！？!?；;])\s*|\n+")


def _split_sentences(text: str) -> list[str]:
    """按句末标点切分；过短的碎片并给下一句（避免合成请求太碎）。"""
    parts = [p.strip() for p in _SENT_END.split(text) if p and p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) < 6:
            merged[-1] += p
        else:
            merged.append(p)
    return merged or ([text.strip()] if text.strip() else [])


class EdgeTts:
    """Edge-TTS 引擎：中文音色自然、免费、需联网。pygame-ce 提供可打断的 mp3 播放。"""

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+10%") -> None:
        self._voice = voice
        self._rate = rate
        self._gen = 0               # 代次：stop/speak 都会递增，作废旧任务
        self._lock = threading.Lock()
        self._mixer_ready = False
        self._pending: list[tuple[list[str], "object"]] = []  # 连播排队（(句子批, 播完回调)）
        self._last_speak_call = 0.0  # 上一条 speak() 调用时刻（连播判定窗口用）
        self._run_active = False     # 播放线程存活（覆盖"已启动未出声"的合成窗口）

    # ---- 公开接口 ----
    def speak(self, text: str, on_done=None) -> None:
        """播报文本。on_done：该批句子全部播完后的回调（播完率埋点用，可为 None）。

        连播排队（M3）：若正在播报且距上一条 speak() 调用 <3s，新句排队接在
        播放队列尾而非顶断——快速连播常见于答案拆条，顶断会把前一条吃掉。
        显式 stop()（用户按键打断）语义不变：停播并清空队列。
        """
        sentences = _split_sentences(text or "")
        if not sentences:
            return
        now = time.time()
        with self._lock:
            rapid = (now - self._last_speak_call) < 3.0
            self._last_speak_call = now
            busy = self.speaking() or bool(self._pending) or self._run_active
            if busy and rapid:
                self._pending.append((sentences, on_done))
                print(f"[tts] 连播排队（第{len(self._pending)}条）: {sentences[0][:30]}",
                      flush=True)
                return
            self._gen += 1          # 顶断路径：新作废旧任务（不经过 stop() 以免清队列逻辑重复）
            gen = self._gen
            self._pending.clear()
        try:
            if self._mixer_ready:
                import pygame
                pygame.mixer.music.stop()
        except Exception:
            pass
        threading.Thread(target=self._run, args=(gen, sentences, on_done),
                         name="tts", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._pending.clear()
        try:
            if self._mixer_ready:
                import pygame
                pygame.mixer.music.stop()
        except Exception:
            pass

    def speaking(self) -> bool:
        try:
            import pygame
            return self._mixer_ready and pygame.mixer.music.get_busy()
        except Exception:
            return False

    def warmup(self) -> None:
        """预热 edge-tts 连接（DNS/TLS 会话复用），压低首次播报延迟。静音，只合成不播。"""
        def _warm() -> None:
            path = _TTS_DIR / "warmup.mp3"
            try:
                asyncio.run(self._synth("嗯", path))
                print("[tts] 预热完成", flush=True)
            except Exception as exc:
                print(f"[tts] 预热失败: {exc}", flush=True)
            self._cleanup(path)
        threading.Thread(target=_warm, name="tts-warm", daemon=True).start()

    # ---- 内部 ----
    def _is_current(self, gen: int) -> bool:
        with self._lock:
            return gen == self._gen

    def _run(self, gen: int, sentences: list[str], on_done=None) -> None:
        """播放循环：播完当前批次后取连播排队的下一批续播，直到队列空。"""
        batch, done_cb = sentences, on_done
        with self._lock:
            self._run_active = True
        try:
            import pygame
            if not self._mixer_ready:
                pygame.mixer.init()
                self._mixer_ready = True
            first = True
            while batch is not None:
                q: queue.Queue = queue.Queue()
                threading.Thread(target=self._produce, args=(gen, batch, q),
                                 name="tts-synth", daemon=True).start()
                while True:
                    if not self._is_current(gen):
                        return
                    try:
                        item = q.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is None:
                        break
                    if first:
                        print(f"[tts] 播报: {batch[0][:40]}（共{len(batch)}句）", flush=True)
                        first = False
                    try:
                        pygame.mixer.music.load(str(item))
                        pygame.mixer.music.play()
                        while pygame.mixer.music.get_busy():
                            if not self._is_current(gen):
                                return   # 被打断，stop() 已停音乐
                            time.sleep(0.05)
                        try:
                            pygame.mixer.music.unload()  # 释放文件句柄以便删除
                        except Exception:
                            pass
                    except Exception as exc:
                        print(f"[tts] 播放失败: {exc}", flush=True)
                    self._cleanup(item)
                # 本批完整播完（gen 仍有效）→ 播完回调（埋点）→ 续播下一批
                if self._is_current(gen) and done_cb:
                    try:
                        done_cb()
                    except Exception:
                        pass
                with self._lock:
                    nxt = self._pending.pop(0) if self._pending else None
                if nxt is None:
                    return
                batch, done_cb = nxt
                print(f"[tts] 连播续播: {batch[0][:40]}（共{len(batch)}句）", flush=True)
        except Exception as exc:
            print(f"[tts] 播报失败: {exc}", flush=True)
        finally:
            with self._lock:
                if self._gen == gen:
                    self._run_active = False

    def _produce(self, gen: int, sentences: list[str], q: "queue.Queue") -> None:
        """合成生产者：逐句合成，产物路径进队列；被打断即停。"""
        for i, s in enumerate(sentences):
            if not self._is_current(gen):
                return
            path = _TTS_DIR / f"tts_{gen}_{i}_{int(time.time() * 1000)}.mp3"
            try:
                asyncio.run(self._synth(s, path))
                if not self._is_current(gen):
                    self._cleanup(path)
                    return
                q.put(path)
            except Exception as exc:
                print(f"[tts] 合成失败(句{i}): {exc}", flush=True)
                self._cleanup(path)
        q.put(None)  # EOF

    async def _synth(self, text: str, path: Path) -> None:
        import edge_tts
        _TTS_DIR.mkdir(parents=True, exist_ok=True)
        await edge_tts.Communicate(text, voice=self._voice, rate=self._rate).save(str(path))

    @staticmethod
    def _cleanup(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
