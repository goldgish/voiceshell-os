# -*- coding: utf-8 -*-
"""一键自测：把项目里所有"不依赖模型/不依赖声卡"的回归自测跑一遍。

用法: python app/testsets/_selftest_all.py

包含：
  1) _selftest_judge_parse.py   judge 输出解析（坏 JSON 也能出分）
  2) _selftest_tts_queue.py     M3 连播不被顶断（量播放时长）
  3) _check_prompt_drift.py     插件提示词 与 app/voice_prefix.py 是否同义

为什么要有它：本项目的历史故障大多是"测试绿、线上却没这回事"——
两份提示词各改一半、跑器把审批查询当播报、judge 解析太脆被当成模型不行。
这些都能在 3 秒内用自测挡住，不必等 5 分钟的全量评测。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = [
    ("judge 解析", HERE / "_selftest_judge_parse.py"),
    ("TTS 连播", HERE / "_selftest_tts_queue.py"),
    ("提示词漂移", HERE / "_check_prompt_drift.py"),
]


def main() -> int:
    failed: list[str] = []
    for name, path in TESTS:
        proc = subprocess.run([sys.executable, str(path)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        ok = proc.returncode == 0
        tail = [ln for ln in (proc.stdout or "").strip().splitlines() if ln.strip()]
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {tail[-1] if tail else '(无输出)'}")
        if not ok:
            failed.append(name)
            for ln in tail[-6:]:
                print(f"      {ln}")
    print()
    print(f"自测汇总: {len(TESTS) - len(failed)}/{len(TESTS)} 通过")
    if failed:
        print("失败项: " + "、".join(failed))
        return 1
    print("ALL_CHECKS_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
