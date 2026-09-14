# -*- coding: utf-8 -*-
"""硬兜底播报文案（单一事实来源）。

生产（`app/app.py` 的 `_hb_loop`）与评测跑器（`testsets/_run_tests.py`）必须说同一句话，
否则会出现"测试通过、线上却是另一套文案"的假绿。这里收口成唯一来源，
思路与 `voice_prefix.py` 相同（提示词也只留一份）。

M4：兜底不再只说"还在忙"。带上当前在忙什么，用户听到的是
"还在忙，正在写入 snake.html"，而不是一句无从判断的空话。

注意：标题来自 app 的 `_tool_title`，对 pwsh 工具而言那可能是英文 description
（如 "Rebuild plugin bundle..."），直接念就变成英文长句——所以只接受
**短且含中文**的标题，其余一律退回通用文案。
"""
from __future__ import annotations

import re

#: 没有可用上下文时的通用文案（保持与历史一致，便于回归比对）
GENERIC = "还在忙，有进展我会说。"

_MAX_WHAT = 16
_CJK = re.compile(r"[\u4e00-\u9fff]")


def hard_fallback_text(what: str = "") -> str:
    """按"当前在忙什么"拼硬兜底文案；标题不可用时退回通用文案。

    what：中文短语或标题，如 "写入 snake.html"、"查资料"。
    """
    tip = (what or "").strip().replace("\n", " ")
    if not tip or len(tip) > _MAX_WHAT or not _CJK.search(tip):
        return GENERIC
    return f"还在忙，正在{tip}。有进展我会说。"
