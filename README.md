# 语音秘书 · dsh-voice-secretary

给 DSH（deepseek-harness）装一条语音通道：按住蓝牙遥控器的语音键说话，松手就把这句话交给秘书；
秘书干活的时候用中文语音汇报进展，你在旁边听着就行。

- **是插件，不是独立程序**：它挂在 DSH 上（Web 实例 / TUI 都能用），不另起一套界面，也不占用 DSH 的操作方式。
- **纯语音**：没有输入框，不用打字。语音是唯一的交互方式。
- **Windows**：遥控器走 BLE（WinRT），目前只在 Windows 上跑通。

---

## 它是怎么搭起来的

两半，一个仓库：

```
遥控器（按住语音键）
   │  BLE / ATVV
   ▼
app/link.py ──WAV──▶ app/asr.py ──文字──▶ POST http://127.0.0.1:4177/voice/input
（连接层）              （GLM-ASR）                    │
                                                      ▼
                              DSH Web 实例 · 秘书会话（干活 + 调工具）
                                                      │
                                         MCP 工具 mcp__voice__speak
                                                      ▼
                                          app/tts.py（Edge-TTS）──▶ 扬声器
```

| 路径 | 是什么 |
| --- | --- |
| `app/` | Python 运行时：BLE 连接层、ASR、TTS、本地 HTTP 服务 |
| `dsh-voice-input/` | DSH 插件（TypeScript）：sidebar 的语音状态灯、`speak` 工具卡片、`/voice/*` 路由 |
| `setup.py` / `setup.bat` | 初始化向导：查环境、装依赖、收 key、把插件挂进 DSH |
| `launcher.ps1` / `启动.bat` / `停止.bat` | 一键启动 / 停止 |

插件已附带构建产物（`dsh-voice-input/lib/`），下载即用。改过 `src/` 才需要 `npm install && npm run build`。

## 需要什么

- Windows 10 / 11
- Python 3.11+
- Node.js 18+ 和 pnpm（DSH 用 pnpm 管 profile 依赖）
- DSH 运行时：`pip install deepseek-harness-runtime-bin`，装完**至少跑一次 `dsh`**，让它把 `~/.dsh` 铺出来
- 一个支持 ATVV 的 BLE 遥控器：当前适配小米 RC001-MS（`VID_0127` / `PID_32B8`）
- 一个智谱 API Key（只用于语音转文字）

## 三步上手

**1. 双击 `setup.bat`**

向导会依次：体检（Python / Node / DSH）→ 装 Python 依赖 → 收你的 GLM key 写进 `app/.env`
→ 把插件挂进 `~/.dsh/profiles/web`（动过的文件留 `*.bak-setup` 备份）。
可以重复跑，已有的配置会被认出来，不会重复添加。

**2. 双击 `启动.bat`**

它拉起三样东西：BLE 连接层、语音运行时、DSH Web 实例；顺手在桌面放一个「语音输入」快捷方式，
并自动打开 4177 页面（token 只在实例日志里，脚本会自己抓）。

**3. 遥控器配对一次**

Windows 设置 → 蓝牙 → 添加设备 → 配对遥控器。之后按住语音键说话，松手即发送。

页面左侧栏底部会出现「**语音在线**」绿灯。绿了就是通了。

## 关于 Key

| 环节 | 方案 | 要不要 key |
| --- | --- | --- |
| 语音转文字（ASR） | 智谱 GLM-ASR | **要**。到 <https://open.bigmodel.cn/usercenter/apikeys> 申请，新账号带免费额度，按音频秒数计费，日常用花不了几毛钱 |
| 语音播报（TTS） | 微软 Edge-TTS | 不要。免费、免登录，但要联网 |

`app/.env`（向导会生成，也可以手改，改完重启 `启动.bat`）：

```env
ZHIPU_API_KEY=你的key
```

## 秘书手上有哪些工具

插件会挂一个 MCP 服务器 `voice`，秘书会话据此拿到：

| 工具 | 干什么 |
| --- | --- |
| `speak` | 口播一句话给你听——这是唯一的输出通道 |
| `list_pending_approvals` / `decide_approval` | 念出待审批的事、替你签字 |
| `list_sessions` / `new_session` / `switch_session` / `send_to_session` / `stop_session` / `read_session` | 会话调度：开新会话、切过去、派活、停当前轮、读会话 |

秘书会话的 id 固定为 `session-voice-secretary`，工作目录是用户主目录。
想换的话用环境变量 `VOICE_SECRETARY_SESSION` / `VOICE_SECRETARY_CWD`；
语音运行时目录也可以用 `VOICE_APP_DIR` 指到别处。

## 常见问题

**侧边栏那个是状态灯，不是录音键。**
录音发生在桌面端（遥控器 → `link.py`），浏览器这一侧拿不到音频，也没必要拿。
灯只回答一个问题：本机的语音运行时在线没有。

**灯不绿。**
按顺序看：`启动.bat` 跑了吗 → 4177 端口起了吗 → `app/voice_http.port` 指的端口上有服务吗。
都不是，重跑一次 `setup.bat`。

**遥控器按了没反应。**
多半是残留的 `link.py` 在抢 BLE 连接（手动杀过 `app.py` 容易留下孤儿进程）。
先双击 `停止.bat` 再启动。另外确认遥控器在 Windows 蓝牙里已配对成功。

**想换别的遥控器。**
只要是 ATVV 协议（服务 UUID `ab5e0001-5a21-4f05-bc7d-af01f617b664`）就能用：
把 [app/atvv.py](app/atvv.py) 里的 `_HW_TOKEN` 改成你的 VID/PID 即可。

**4177 被占。**
那是本机别的 DSH Web 实例在用。先 `停止.bat`，或者用 `VOICE_HOST_PORT` 换个端口。

**看运行日志。**
`app/web_stdout.log`（实例输出，含带 token 的地址）、`app/web_stderr.log`、`app/app_debug.log`。

## 卸载

把 `~/.dsh/profiles/web/cordis.patch.yml` 与 `package.json` 恢复成 `*.bak-setup` 备份
（或手工删掉 `mcp-voice` 段和 `@local/dsh-voice-input` 依赖），然后删掉本仓库目录即可。

## License

MIT
