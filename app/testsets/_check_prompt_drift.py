# -*- coding: utf-8 -*-
"""提示词单一来源一致性检查：插件里的 PROMPT_TEXT 与 app/voice_prefix.py 是否同义。

背景：语音播报纪律存在两份——插件（生产：host 级 systemPrompt 注入）与
app/voice_prefix.py（评测跑器用）。两份靠人工同步，一旦漂移就会出现
"测试量的规则不是线上跑的规则"。这个脚本把漂移量算出来，并区分
**允许的差异**（工具名写法）与**真实漂移**（条款增删）。

用法: python app/testsets/_check_prompt_drift.py
输出: 控制台 + app/testsets/_prompt_drift.txt（UTF-8，便于用编辑器看）
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT = APP_DIR.parent
PLUGIN_SRC = ROOT / "dsh-voice-input" / "src" / "index.ts"
OUT = Path(__file__).resolve().parent / "_prompt_drift.txt"

sys.path.insert(0, str(APP_DIR))
from voice_prefix import VOICE_PREFIX  # noqa: E402


def extract_prompt_text(src: str) -> str:
    """抠出 src/index.ts 里的 `const PROMPT_TEXT = ` ... `` 模板字面量。

    边界取"下一个块注释之前的最后一个反引号"——直接找 "\\n`" 会被后面
    SECRETARY_PREFIX、类型定义里的反引号带跑（第一版就这么翻过车，
    把 7621 字符的代码当成提示词比了一通）。
    """
    start = src.index("const PROMPT_TEXT = `") + len("const PROMPT_TEXT = `")
    seg_end = src.index("/**", start)
    segment = src[start:seg_end]
    return segment[: segment.rindex("`")]


def norm(text: str) -> str:
    """归一化：工具名写法统一、去掉所有空白（排版差异不算漂移）。"""
    text = text.replace("mcp__voice__speak", "speak").replace("`speak`", "speak")
    return re.sub(r"\s+", "", text)


def main() -> int:
    plugin_raw = extract_prompt_text(PLUGIN_SRC.read_text(encoding="utf-8"))
    a, b = norm(VOICE_PREFIX), norm(plugin_raw)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()

    lines = [
        "# 提示词漂移检查",
        f"app/voice_prefix.py : {len(a)} 字符（归一化后）",
        f"插件 PROMPT_TEXT    : {len(b)} 字符（归一化后）",
        f"完全相同            : {a == b}",
        f"相似度              : {ratio:.4f}",
        "",
    ]
    if a != b:
        lines.append("## 差异段（左=python/跑器，右=插件/生产）")
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
            if tag == "equal":
                continue
            lines.append(f"[{tag}] 跑器: {a[i1:i2][:200]}")
            lines.append(f"        插件: {b[j1:j2][:200]}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"identical={a == b} ratio={ratio:.4f} -> {OUT.name}")
    return 0 if a == b else 1


if __name__ == "__main__":
    raise SystemExit(main())
