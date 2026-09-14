# -*- coding: utf-8 -*-
"""M3 回归测试：TTS 连播不能被顶断（无需声卡、无需联网，2 秒内跑完）。

为什么要有它：M3 是"用户实际听到的答案是残缺的，而评测记全绿"的那类 bug——
跑器只能看到 speak 调用，看不到声音有没有播完。所以这里把 pygame / edge-tts
换成假的，直接看**播放时长**：快速连播时第一条必须完整播完，不能被打断。

用法: python app/testsets/_selftest_tts_queue.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

PLAY_S = 0.30        # 每条"音频"的假播放时长
RAPID_GAP_S = 0.05   # 两次 speak 的间隔，落在 tts 的 3 秒连播窗口内


class FakeMusic:
    """假 mixer.music：按时间模拟播放，记录每条的播放时长与是否被打断。

    注意：引擎在一次自然播完后调 unload()、被打断时调 stop()，
    所以两处都要"结账"记录时长——第一版只在 stop() 里记，导致正常播完的时长
    根本没被记下来（自测自己先报了个假 FAIL）。
    """

    def __init__(self) -> None:
        self.current: str | None = None
        self.plays: list[dict] = []      # {path, t0, dur}
        self._end = 0.0
        self.stop_calls = 0

    def load(self, path: str) -> None:
        self.current = str(path)

    def play(self) -> None:
        self.plays.append({"path": self.current, "t0": time.time(), "dur": None})
        self._end = time.time() + PLAY_S

    def get_busy(self) -> bool:
        return time.time() < self._end

    def stop(self) -> None:
        self.stop_calls += 1
        self._close()

    def unload(self) -> None:
        self._close()

    def _close(self) -> None:
        if self.plays and self.plays[-1]["dur"] is None:
            self.plays[-1]["dur"] = time.time() - self.plays[-1]["t0"]
        self._end = 0.0

    @property
    def durations(self) -> list[float]:
        return [float(p["dur"] or 0.0) for p in self.plays]


class FakeMixer:
    def __init__(self, music: FakeMusic) -> None:
        self.music = music

    def init(self) -> None:
        pass


def _install_fakes(music: FakeMusic) -> None:
    pygame = types.ModuleType("pygame")
    pygame.mixer = FakeMixer(music)          # type: ignore[attr-defined]
    sys.modules["pygame"] = pygame

    edge_tts = types.ModuleType("edge_tts")

    class Communicate:
        def __init__(self, text: str, voice: str = "", rate: str = "") -> None:
            self.text = text

        async def save(self, path: str) -> None:
            await asyncio.sleep(0.02)         # 假装在合成
            Path(path).write_bytes(b"fake-mp3")

    edge_tts.Communicate = Communicate       # type: ignore[attr-defined]
    sys.modules["edge_tts"] = edge_tts


def _drain(engine, music: FakeMusic, timeout: float = 6.0) -> None:
    """等播放队列空。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not engine.speaking() and not engine._pending and not engine._run_active:
            return
        time.sleep(0.02)


def main() -> int:
    music = FakeMusic()
    _install_fakes(music)

    import tts  # 必须在假模块装好之后导入

    engine = tts.EdgeTts()
    done: list[str] = []

    engine.speak("第一条答案，应该完整播完，不能被第二条顶掉。", on_done=lambda: done.append("a"))
    time.sleep(RAPID_GAP_S)
    engine.speak("第二条答案，接在第一条后面播。", on_done=lambda: done.append("b"))
    _drain(engine, music)

    assert len(music.plays) == 2, f"应播两条，实际 {len(music.plays)}"
    first_len = music.durations[0] if music.durations else 0.0
    assert first_len >= PLAY_S * 0.8, (
        f"第一条只播了 {first_len:.2f}s（应 ≈{PLAY_S}s）：连播被顶断，M3 回归了")
    assert done == ["a", "b"], f"播完回调顺序不对: {done}"
    print(f"连播 OK：两条都播完，第一条实际 {first_len:.2f}s，回调 {done}")

    # 显式 stop()（用户按键打断）：语义不变，必须真的停
    music2 = FakeMusic()
    _install_fakes(music2)
    engine2 = tts.EdgeTts()
    engine2.speak("这条应该被用户打断。")
    deadline = time.time() + 3
    while time.time() < deadline and not music2.plays:
        time.sleep(0.02)
    engine2.stop()
    time.sleep(0.1)
    cut = music2.durations[0] if music2.durations else 0.0
    assert cut < PLAY_S, "stop() 没能打断播放"
    print(f"打断 OK：stop() 后第一条只播了 {cut:.2f}s")

    print("ALL_CHECKS_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
