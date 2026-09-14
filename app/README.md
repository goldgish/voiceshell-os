# 语音 Coding · 项目说明

一个纯语音交互的 AI Agent 浮窗。按住遥控器语音键说话 → 转写 → 交给 DSH Agent 执行 → 浮窗实时展示思考与工具动作 → TTS 口语汇报结果。**无任何文字输入路径**——浮窗本身就是对话界面。

## 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│  遥控器（RC001-MS BLE）                                      │
└──────────────┬──────────────────────────────────────────────┘
               │ ATVV BLE 协议
┌──────────────▼─────────────────┐
│  连接层  link.py（独立子进程）  │  维持 BLE 长连，按下/松开语音键
│   - ATVV 协议层（atvv.py）       │  自动收 ADPCM 帧 → 解码 PCM → 写 WAV
│   - 不碰 UI / 不做转写            │  stdout JSON-line 与父进程通信
└──────────────┬─────────────────┘
               │ {"event":"session_pcm","wav":"..."}
┌──────────────▼─────────────────┐
│  主程序  app.py（pywebview）     │  编排 + 事件总线
│   - 托盘（pystray）              │  VoiceApp._emit() 广播到浮窗 + 落地页
│   - 浮窗（overlay.html @ WebView2）│
│   - 落地页（landing.py + .html）  │
└───┬──────────┬─────────────┬───┘
    │          │             │
    │  ┌───────▼──────┐  ┌───▼────────────┐
    │  │ ASR  asr.py   │  │ TTS  tts.py      │
    │  │ 智谱 GLM-ASR  │  │ Edge-TTS+pygame  │
    │  │ 非流式（省开销）│  │ 句子级流水线+打断 │
    │  └──────┬───────┘  └──────▲───────────┘
    │         │ text             │ speak(text)
┌───▼─────────┴──────────────────┴─────────────┐
│  Agent 层  dsh_client.py                       │
│   - 起 DSH Host（node dsh web --port 0）       │
│   - HTTP 一元调用：session/prompt              │
│   - WS 流式：session/follow（snapshot/event/   │
│     assistant-stream 三类帧）                  │
└────────────────────────────────────────────────┘
```

## 文件清单

| 文件 | 职责 |
|---|---|
| [app.py](file:///c:/Users/60512/语音coding/app/app.py) | 主程序。pywebview 浮窗 + pystray 托盘 + 编排（事件总线、投影层、TTS 触发） |
| [dsh_sdk_bridge.py](file:///c:/Users/60512/语音coding/app/dsh_sdk_bridge.py) | DSH 官方 SDK 桥。`deepseek-harness-sdk` 驱动本地 dsh（`--profile sdk`），回合串行、会话换新回退、残留进程扫杀 |
| [dsh_voice_mcp.py](file:///c:/Users/60512/语音coding/app/dsh_voice_mcp.py) | 语音 MCP 服务器。`speak` 工具 → HTTP 回调 app 内 TTS 引擎播报 |
| [voice_runtime.patch.yml](file:///c:/Users/60512/语音coding/app/voice_runtime.patch.yml) | 运行时补丁：挂 `dsh-mcp-client` 接入语音 MCP 服务器 |
| [dsh_local.cmd](file:///c:/Users/60512/语音coding/app/dsh_local.cmd) | 本地 dsh 启动包装（`chcp 65001` 保 SDK 管道编码干净） |
| [link.py](file:///c:/Users/60512/语音coding/app/link.py) | 连接层子进程。纯 ATVV 协议，不碰 UI/转写；stdin 接 stop，stdout 出 JSON 事件 |
| [atvv.py](file:///c:/Users/60512/语音coding/app/atvv.py) | ATVV BLE 协议实现（ctrl + audio 通道、ADPCM 解码） |
| [asr.py](file:///c:/Users/60512/语音coding/app/asr.py) | 智谱 GLM-ASR 转写。非流式整体返回（省 SSE 开销，比流式快） |
| [tts.py](file:///c:/Users/60512/语音coding/app/tts.py) | Edge-TTS 合成 + pygame-ce 播放。句子级流水线、支持打断 |
| [overlay.html](file:///c:/Users/60512/语音coding/app/overlay.html) | 浮窗 UI。状态行 + 波形 + 对话/思考/工具卡片 |
| [landing.py](file:///c:/Users/60512/语音coding/app/landing.py) | 落地页本地 HTTP + SSE。同源事件流 + `/voice/speak` 回调入口 |
| [landing.html](file:///c:/Users/60512/语音coding/app/landing.html) | 落地页 UI。状态指示 + 对话/思考/工具卡片 |
| [.env](file:///c:/Users/60512/语音coding/app/.env) | `ZHIPU_API_KEY`（ASR 用）+ `DEEPSEEK_API_KEY`（SDK 模型用） |
| `voice_session.id` | 专属语音会话 id 持久化（SDK 0.1.5rc1 不支持跨进程 resume，重启后首次对话自动换新） |
| `_voice_home/` | SDK 隔离运行时 home（profile/插件/会话/权限预设，首次启动自动物化） |
| `app_debug.log` / `dsh_frames.log` | 关键事件落盘 / session.event 帧样本 |

## 核心交互流程

1. **按下语音键** → link.py 收到 ATVV `STREAM_START`，app 立即 `tts.stop()`（打断即应答）+ 显示浮窗
2. **录音中** → link.py 持续上送 PCM 帧，app 计算 RMS 振幅发 `frame` 事件，浮窗画实时波形 + 计时
3. **松手** → link.py 收 `STREAM_STOP`，解码整段 PCM 写 WAV，发 `session_pcm` 事件
4. **转写** → app 用 GLM-ASR 转写 WAV（~1s），发 `user_text` → 浮窗出用户气泡 → 落地页落历史
5. **发送给 Agent** → `_VOICE_PREFIX + text` 经 SDK 桥（`session/prompt`）发给本地 dsh agent
6. **回合流接收**（官方 `session.event` 持久事件实时回调）：
   - `step/start` `tool/call` `tool/result` `assistant/message` → **投影层**转卡片（思考/工具/回复气泡）
   - `session.status: idle` → 回合完成判定
7. **语音播报** → agent 主动调用 MCP `speak` 工具（`mcp__voice__speak`）→ HTTP 回调 app 内 TTS；agent 未播时 app 播最终总结兜底 → 20s 后浮窗自动隐藏

## 投影层（参考 dsh-TUI）

把 DSH `event` 帧投影成浮窗卡片，让长任务**边做边展示**，不再"默默几分钟才出结果"。

**卡片类型**（[app.py L660-690](file:///c:/Users/60512/语音coding/app/app.py#L660-L690) `_project_assistant_message`）：

| block.type | 卡片 | TTS |
|---|---|---|
| `reasoning` | 思考卡片（灰色斜体，压成 100 字 preview） | 提取【汇报】段播报 |
| `tool-call` | 工具卡片（青色，spinner→check/err） | **不播报**（避免噪音） |
| `text` | 罕见，作为思考展示 | 不播报 |

**工具卡片标题**（[app.py L739-791](file:///c:/Users/60512/语音coding/app/app.py#L739-L791) `_tool_title`，按 variant 分派，参考 dsh-TUI `classifyTool`）：

| 工具 | 标题生成 |
|---|---|
| `write`/`edit` | "写入 mesh.js" / "编辑 mesh.js"（args.path 取文件名） |
| `pwsh`/`bash` | args.description > 命令首行 |
| `read_image` | "查看 render.png"（args.path） |
| `read` | "读取 mesh.js" |
| `grep`/`glob`/`search` | "搜索 pattern" |
| `present` | "交付成果" |
| 兜底 | args.description > "工具调用" |

**TTS 口语播报机制（核心创新，MCP agent 驱动）**：

TTS 不再每个工具都"正在…"（噪音），改为**agent 主动播报**：
- runtime 经 [voice_runtime.patch.yml](file:///c:/Users/60512/语音coding/app/voice_runtime.patch.yml) 挂 `dsh-mcp-client`，把 [dsh_voice_mcp.py](file:///c:/Users/60512/语音coding/app/dsh_voice_mcp.py) 的 `speak` 工具注册为 `mcp__voice__speak`
- [app.py](file:///c:/Users/60512/语音coding/app/app.py#L73-L87) `_VOICE_PREFIX` + speak 工具描述共同约束播报框架（与开源经验对齐：codex-voice-reply 的 decision-first、Alexa 的 one-breath/≤30 字）
- `speak` 工具 → POST 落地页 `/voice/speak` → app 内 EdgeTts 发声（支持按键打断）
- 工具卡片标题不触发 TTS；agent 整回合没播时，app 用 `_extract_summary` 播最终总结兜底

**播报框架（什么值得播）**：

| 时刻 | 播不播 | 要求 |
|---|---|---|
| 开场 | 播 | agent 播一句理解确认（如"收到，这就去调研中国好茶"） |
| 里程碑 | 播 | 只报"已完成/已产出"，**不报预告**；概念级口语 ≤30 字 |
| 出问题 | 播 | decision-first：先说卡在哪、需要用户做什么 |
| 长任务静默 >60s | 播（心跳） | app nudge agent 用 speak 报一句概念进度（[app.py](file:///c:/Users/60512/语音coding/app/app.py#L543-L556) `_hb_loop`） |
| 收尾 | 播 | 结果 + 产物位置 + 下一步决策；详情/列表看屏幕 |
| 每次工具调用 / 屏幕已有细节 | 不播 | 浮窗卡片负责 |

**prompt 约束**（[app.py](file:///c:/Users/60512/语音coding/app/app.py#L73-L84) `_VOICE_PREFIX`）：
- 自主执行，不反问
- 中文思考 + 中文回复
- 阶段性进展用 `speak` 工具播报，概念级口语（说"正在渲染"不说"执行 node render.js"）
- 完成后最后用 `speak` 播报结果总结

## 浮窗 UI 结构（[overlay.html](file:///c:/Users/60512/语音coding/app/overlay.html)）

- **状态行**：图标（dot/spinner/check/err）+ 标签 + 计时 + 提示
- **波形**：Canvas 实时画 56 条能量柱（按下录音时绿青渐变，松手归零）
- **对话区**：用户气泡（绿）/ 助手气泡（灰）/ 思考卡片（灰斜体折叠）/ 工具卡片（青 spinner→check）
- **限制**：最多 40 条气泡

**Win32 窗口样式**（[app.py L220-267](file:///c:/Users/60512/语音coding/app/app.py#L220-L267) `setup_native`）：
- `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`：不抢焦点、不进任务栏
- `SetWindowRgn` 裁圆角（替代 pywebview transparent 的白框问题）
- `SW_SHOWNA`：显示但不激活
- 进程级 Per-Monitor DPI 感知（避免 150% 缩放下坐标混乱）

## 落地页（[landing.py](file:///c:/Users/60512/语音coding/app/landing.py) + [landing.html](file:///c:/Users/60512/语音coding/app/landing.html)）

- 本地 HTTP + SSE，启动后自动 `webbrowser.open`
- 与浮窗同源事件流（VoiceApp._emit 同时广播到两边）
- 历史持久化在 `history.json`（≤40 条）
- 纯展示，**无文字输入**

## SDK 桥（[dsh_sdk_bridge.py](file:///c:/Users/60512/语音coding/app/dsh_sdk_bridge.py) + 官方 Python SDK）

- `deepseek-harness-sdk`（pipe 版与本地 dsh 同为 0.1.5rc1）驱动 **本地 dsh** `--profile sdk`（dsh-base 全量工具树：pwsh/skill/subagent/plan/web 检索）
- **必须走本地 dsh**：捆绑单文件运行时的 pwsh 工具损坏（`--profile is required`）
- 隔离 home `_voice_home/`：首次启动用捆绑运行时 `--dump-default-config` 物化 profile；`settings.yaml` 固定 `danger-full-access`（纯语音场景无法逐条审批）
- 重依赖：`voice_runtime.patch.yml` 挂 `dsh-mcp-client` → 语音 MCP 服务器（`speak` 工具）
- `on_notification` 在回合线程同步回调：`session.event`（step/tool/assistant 全量持久事件）实时投影、`session.status: idle` 判定回合完成
- 回合串行（`_turn_lock`）；启动前扫杀残留 runtime（孤儿 node 会持锁占用 home）
- **会话** `voice_session.id`：进程内复用保留上下文；SDK 0.1.5rc1 不支持跨进程 resume，重启后第一次对话自动换新（丢历史，官方支持后再恢复）

## 关键约束（Lessons Learned）

1. **纯语音交互**：无任何文字输入路径，浮窗本身就是对话界面
2. **不抢焦点**：`WS_EX_NOACTIVATE + SW_SHOWNA`，按下语音键不切走当前焦点窗口
3. **20s 自动隐藏**：回复完成后浮窗 20s 后隐藏，避免长时间遮挡
4. **对话历史 ≤40 条**：浮窗与落地页一致
5. **本地 dsh + chcp 65001**：`pythonw`/隐藏窗口启动时 cmd 管道输出回落系统码页(GBK)，会把 SDK 的 utf-8 管道弄崩（"runtime stdout closed"）。[dsh_local.cmd](file:///c:/Users/60512/语音coding/app/dsh_local.cmd) 里先 `chcp 65001` 再起 node
6. **持久化 `voice_session.id`**：进程内复用；跨进程 resume 暂不可用（见上）
7. **link.py / SDK runtime 子进程孤儿**：app 崩溃时只剩 cmd 外壳被 terminate，node 子进程变孤儿并持锁占 home，后续启动全失败。重启前必须扫杀孤儿（link.py 按 ParentProcessId；runtime 按命令行 `dsh lib bin.js + profile sdk`，bridge 启动时自动扫杀）
8. **`_VOICE_PREFIX` 包装**：约束 agent 自主执行、中文思考、speak 工具节点播报
9. **64 位 Win32 原型**：剪贴板/窗口句柄函数必须声明 `c_longlong`，32 位截断会导致 OverflowError（之前踩过）
10. **ATVV `STREAM_START` 事件**驱动窗口激活比 rawinput 可靠（HID Keyboard 设备错误态下 rawinput 失效）
11. **非流式 ASR 比流式快**：省 SSE 开销，整体返回反而更快
12. **SDK 无 chunk 级流式**：`assistant/message` 整块到达，气泡是段落级追加（打字机效果需前端模拟）

## 启动 / 调试

**一键启动（推荐）**：双击项目根目录的 `启动.bat`，或点桌面快捷方式「语音输入」。

顺序：清孤儿 `link.py` → 起 DSH Web 实例（4177，秘书会话与插件住在那儿）→ 起 `app.py` → 补齐桌面快捷方式 → 打开带令牌的页面。
停止：双击 `停止.bat`。逻辑都在 [launcher.ps1](file:///c:/Users/60512/语音coding/launcher.ps1)。

**手动启动**：
```powershell
pythonw app.py   # 无控制台
# 或
python app.py    # 带 stdout 调试输出
```
> 注意：手动起 `app.py` 前，4177 的实例必须已在跑——转写文本要 POST 到它的 `/voice/input`，实例不在就投不出去。
> 页面访问需要令牌（不带就 401），令牌只在实例 stdout 里打印，所以交给 `启动.bat` 抓。

**单实例守卫**：第二个实例直接退出（`Local\MiRemoteVoiceCodingApp` mutex）。

**托盘菜单**：打开落地页 / 开机自启 / 退出。

**排障日志**：
- `app_debug.log`：关键事件（按下/转写/播报/回合状态/SDK 就绪）
- `dsh_frames.log`：session.event 帧样本
- `_selftest_*.txt`：`--selftest` 自测运行输出

**自测（不出声按键验证全链路）**：
```powershell
python app.py --selftest "先调用语音播报工具 speak 说你好，然后告诉我当前时间"
# Agent 就绪后自动执行该指令：验证 SDK 桥 + MCP speak + TTS 发声
```

**重启服务**（PowerShell）：
```powershell
# 杀旧进程（含孤儿 link.py 与残留 sdk runtime；runtime 由 bridge 启动时自动扫杀）
Get-CimInstance Win32_Process -Filter "name='pythonw.exe' OR name='python.exe'" |
  Where-Object { $_.CommandLine -match 'app\.py|link\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
# 清日志
Remove-Item app_debug.log, dsh_frames.log, assistant_frames.log -Force -ErrorAction SilentlyContinue
# 启动
Start-Process pythonw.exe -ArgumentList 'app.py' -WorkingDirectory $PWD
```

## 当前状态

- ✅ BLE/ATVV 链路稳定
- ✅ GLM-ASR 转写 ~1s
- ✅ 官方 Python SDK 桥（本地 dsh + sdk profile，替代逆向 HTTP/WS 通道）
- ✅ 浮窗 + 落地页双展示
- ✅ TTS 句子级流水线 + 打断 + MCP speak 工具（agent 驱动播报，实测发声）
- ✅ 投影层：思考卡片 + 工具卡片 + 状态切换（session.event 持久事件直读）
- ⚠️ SDK 0.1.5rc1 跨进程会话 resume 不可用（重启换新会话）
- ⚠️ 模型对 speak 播报约束遵守度待观察（简单回合可能少播/不按节点播）
- 🔧 待优化：personaPrefix 中文约束（替代逐条前缀，省 token）、skill 化汇报约定、跨进程会话恢复跟进官方

## 设计原则

1. **语音优先**：TTS 是主反馈通道，浮窗是视觉补充
2. **概念级汇报**：TTS 说概念不说动作（说"渲染图片"不说"执行 node render.js"）
3. **不抢焦点**：浮窗任何操作都不能切走用户当前窗口焦点
4. **容错**：follow 循环任何异常都不能杀循环；TTS 失败只打日志不抛
5. **复用 dsh-TUI 思路**：projection / transcript / agent-view 的卡片折叠、preview、variant 分派、reasoning 折叠时机
6. **最小复杂度**：浮窗窄、语音场景不需要展开详情（diff/search/image card 都折叠）
