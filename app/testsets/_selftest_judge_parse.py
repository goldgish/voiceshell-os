# -*- coding: utf-8 -*-
"""judge JSON 解析的自测（无需 bridge、无需启 dsh）。

用法: python app/testsets/_selftest_judge_parse.py

背景：20260913 全量跑里 10 例有 5 例 judge 解析失败，原因不是模型写不出 JSON，
而是 reason 里带了未转义的英文双引号（如 无"死寂"也无连珠炮），JSON 在那一列断掉。
这里用真实坏样本锁住行为，防止以后又把"解析太脆"误读成"模型不行"。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import _run_tests as rt  # noqa: E402

# 坏样本：B4 的 reason 里夹了裸双引号 —— 与线上失败样本同构
BAD = (
    '{"B1":{"score":5,"reason":"纯问答省开场合理，2秒即开口"},'
    '"B2":{"score":4,"reason":"口语概念级讲瑞利散射，只略偏长"},'
    '"B3":{"score":5,"reason":"如实作答无假功"},'
    '"B4":{"score":5,"reason":"全程仅3.2秒一条播报，无死"寂"也无连珠炮"},'
    '"B5":{"score":5,"reason":"答案亲口说清了"}}'
)
GOOD = ('{"B1":{"score":5,"reason":"ok"},"B2":{"score":5,"reason":"ok"},'
        '"B3":{"score":5,"reason":"ok"},"B4":{"score":5,"reason":"ok"},'
        '"B5":{"score":5,"reason":"ok"}}')
TRUNCATED = '{"B1":{"score":5,"reason":"缺了后面几个维度"'


def main() -> int:
    bad = rt._parse_judge_json(BAD)
    assert bad.get("_repaired") is True, f"坏样本没走修复路径：{bad}"
    assert [bad[k]["score"] for k in ("B1", "B2", "B3", "B4", "B5")] == [5, 4, 5, 5, 5], bad
    print("坏样本修复 OK:", [bad[k]["score"] for k in ("B1", "B2", "B3", "B4", "B5")])
    print("  B4 理由（截到断点）:", bad["B4"]["reason"])

    good = rt._parse_judge_json(GOOD)
    assert "_repaired" not in good and good["B1"]["score"] == 5, good
    print("好样本走严格解析 OK")

    trunc = rt._parse_judge_json(TRUNCATED)
    assert "error" in trunc and len(trunc.get("raw", "")) > 0, trunc
    print("残缺样本如实报错 OK:", str(trunc["error"])[:50])
    print("ALL_CHECKS_PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
