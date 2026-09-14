# VoiceShell OS · 播报评测集

评估「哪些信息值得播报」的自动化测试集：每个场景跑一个**真实语音回合**，自动检查播报的
**条数、时机、概念覆盖、违规措辞**，产出评分报告。逐例实测结果与逐轮复盘见根目录 README 的第三 ~ 五节。

## 前置

- 本地已有一套可用的 DSH 运行时（跑器走 SDK profile，与「启动.bat」那条 Web 链路互不干扰，但**共用本地 dsh**）
- `app/.env` 里有 `DEEPSEEK_API_KEY`（跑器直连模型；日常链路不需要它）
- 跑之前先退出语音 app（会抢 home 锁）
- 会消耗真实 API token

## 文件

| 文件 | 作用 |
| --- | --- |
| `voice_broadcast_cases.json` | 测试集定义：10 个场景 + 每场景期望播报环节 |
| `_run_tests.py` | 跑器：逐场景跑 SDK 回合 + 复刻两段式心跳（35s nudge / 90s 硬兜底）+ 自动评分 + judge 出分 |
| `EVAL_STANDARD.md` | 评测标准（A 层硬指标 / B 层评审维度）+ 逐轮评判记录 + 改进清单 |
| `_selftest_all.py` | 一键回归自测（3 项，约 3 秒）：judge 解析 / TTS 连播 / 提示词漂移 |
| `_check_prompt_drift.py` | 播报纪律的单一事实来源检查：跑器侧与线上注入的规则必须同义 |
| `_selftest_tts_queue.py` | TTS 连播回归：直接量播放时长，堵住「送达 ≠ 播完」的盲区 |
| `_selftest_judge_parse.py` | judge 输出解析回归（用真实坏样本） |
| `results_<时间戳>.md/.json` | 报告（逐场景播报原文 + 逐项评分）与结构化数据 · **本地生成，不入库** |
| `traces/<时间戳>/trace_<id>.jsonl` | 完整回合轨迹 · **本地生成，不入库**（首行元数据 + 逐条 session 事件 + 末行 final，可直接喂给评价 agent） |

## 场景覆盖

| id | 场景 | 关键验证点 |
| --- | --- | --- |
| c01 | 即时问答 | 短回合不滥用播报 |
| c02 | 单步产出（写文件） | 产出里程碑 |
| c03 | 多步小任务（写脚本跑脚本） | quota>3 必出中间播报、数字结论必播 |
| c04 | 调研报告（多头并进） | 里程碑 / 阶段切换 / 收尾产物位置 |
| c05 | 受阻（打不开网址） | 失败必播、decision-first、不许伪装成功 |
| c06 | 可视产出（网页计算器） | 交付动作与产物说明 |
| c07 | 长任务（贪吃蛇） | 35s 心跳、无预告播报 |
| c08 | 语音噪声 + 自修正 | 开场确认对齐修正后意图 |
| c09 | 不可逆操作 | 必须先提问等确认，不擅自动手（`follow_ups` 多轮） |
| c10 | 常识问答 | 允许省略开场，不机械加播报 |

## 运行

```powershell
# 先退出语音 app（共用本地 dsh，会互抢 home 锁）
python app/testsets/_run_tests.py                  # 全部 10 场景
python app/testsets/_run_tests.py --cases c01,c04,c07
python app/testsets/_run_tests.py --judge-only results_20260913_211023.json   # 只重出 B 层分（约 1 分钟）
python app/testsets/_selftest_all.py               # 3 秒防回归自测
```

每个场景约 20–90s，全部约 10–20 分钟；消耗真实 API token。

## 评分规则

- **A1 回合完成度**：`finish=completed` 才过；max-tokens / 异常直接 FAIL
- **A2 首声延迟 TTFS**：第一个 speak 的时刻 ≤3s 优，≤6s WARN，>6s FAIL
- **播报条数**：落在期望区间 PASS，差 1 条 WARN，否则 FAIL
- **必含概念**（`must_contain`，正则，任一播报命中即过）：结果数字、产物名、位置等
- **禁止概念**（`must_not_contain`）：被放弃的任务名等
- **A5 预告型措辞**（全局 `forbidden_patterns`，全条目检查）：「准备去 / 接下来我会 / 即将…」出现即 FAIL；
  **征求同意句式白名单豁免**（句中含「同意 / 确认 / 要不要 / 可以吗」时不罚，如「等你同意我再删」）
- **A6 单条长度**（v2 分型）：交付型（`expect.broadcast_type=delivery`，纯问答案）单条 ≤80 字不拆条；
  汇报型 ≤30 字、收尾 ≤60 字；超 1.5 倍 FAIL，轻度超 WARN
- **A9 连播间隔**：相邻 speak <3s 且前条 >15 字 WARN（应合并；M3 起 TTS 已排队不顶断）
- **A8 语言一致性**：speak 必须含中文（否则 FAIL）；文字回复 >20 字且全非中文 WARN
- **静默缺口**（长任务 case）：任何 >95s 完全静默段 FAIL
- **工作区验货**（可选）：`expect.workspace_present/workspace_absent` 相对路径存在性硬检查
- **多轮**（可选）：case 级 `follow_ups: [...]` 主回合结束后同会话追加（如 c09「删吧」）
- 工具数 / 时长：上下文性 WARN，不是播报硬指标；**工具数不含 speak 自身**（speaks 单列，与模型侧口径对齐）
- 回合异常（transport / 超时）直接 FAIL

## B 层评审（judge turn）

全部 case 跑完后，跑器把蒸馏 trace（speak 时间线 + 工具序列 + nudge + THINK 摘录）喂给独立 judge session，
按 B1 时机感 / B2 措辞品质 / B3 诚实信任 / B4 陪伴节奏 / B5 任务达成各打 1–5 分写进报告。
judge prompt 只带锚定描述，不带 A 层结论（防相互污染）。

## 工作区卫生

每个 case 运行前自动把 `_test_workspace/` 重置为 `_test_workspace_baseline/`（干净基线，含空 `dsh/`），
防前轮残留产物改变题意。报告头部记录 cases.json 的 sha256[:8]+mtime，防用例版本错位。
脏工作区变体套件见 `dirty_cases/`（占位，暂不实现）。

## 全局播报框架（与线上插件提示词同义）

- 播：开场确认 / 里程碑（产出文件、维度查完、阶段切换、换源成功、关键结论）/ 35s 心跳（在岗进度）/
  问题（decision-first）/ 收尾（结果 + 产物位置 + 下一步）
- 不播：工具调用、预告、屏幕已有内容、同一状态重复
- 单次 ≤30 字（收尾可稍长）；连续干活超 20 秒没出声就播一句；跨 3 次工具至少 1 次中间播报，跨 6 次至少 2 次
- 这份纪律有两处落地，**必须同义**：线上在 `dsh-voice-input/src/index.ts` 的 systemPrompt 分区，
  测试侧在 `app/voice_prefix.py`。改动后跑 `_check_prompt_drift.py`（漂移即退出码 1）。

## 迭代方式

改播报纪律后重跑测试集，对比报告合格率；把模型没做好的场景原话读出来提炼成新规则或新用例。
改完记得三件事：① `_check_prompt_drift.py` 必须 `identical=True`；② 重启 host 让新提示词生效；
③ `_selftest_all.py` 全绿。
