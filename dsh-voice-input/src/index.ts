/** Host 半（Node.js）：
 * 1) systemPrompt 注入语音播报规则（插件自带 prompt，任何挂本插件的 TUI 都获得播报纪律）；
 * 2) webServer 路由：
 *    - GET  /voice/status  探测本地 Python 语音运行时是否在线（读 voice_http.port）
 *    - POST /voice/speak   同源代理到 Python /voice/speak（浏览器直连被 CORS 挡，必须经 host）
 * 3) HITL 语音提醒：审批 / 多选项提问到达时让本机 TTS 出声。
 *
 * HITL 为什么必须在 Host 端而不是浏览器端：浏览器侧 `ctx.remote.$on` 的监听器挂在同一条
 * Cordis waterfall 上、按注册顺序执行，且**先返回结果者否决整条链**；官方 answerer
 * （dsh-client-ui-user-questions / dsh-client-ui-approval）在 roster 里排在前面，会 await
 * 用户的回答而不调用 next()，所以我们这种「只观察不认领」的监听器永远轮不到。
 * Host 端用 `ctx.on(event, fn, { prepend: true })` 抢到链首，播报完再 `return next()`
 * 交回官方转发链路，官方确认 UI 不受影响。顺带也不再依赖浏览器在线。
 *
 * Python 语音运行时目录用 VOICE_APP_DIR 覆盖，默认本项目 app/。
 */
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { IncomingMessage, ServerResponse } from 'node:http'

/** 本插件目录（= lib/index.mjs 的上一级）。 */
const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url))

/**
 * Python 语音运行时目录。默认认仓库里的 app/——插件与运行时同仓发布，
 * 从 lib/ 往上两级就是仓库根，所以 clone 到哪儿都不用改代码。
 * 把运行时装在别处时用 VOICE_APP_DIR 覆盖。
 */
const APP_DIR = process.env.VOICE_APP_DIR ?? resolve(PLUGIN_DIR, '..', '..', 'app')

/** 秘书会话：语音的唯一落点。固定 id，进程重启后由 sessionController 自动唤醒。 */
const SECRETARY_SESSION_ID = process.env.VOICE_SECRETARY_SESSION ?? 'session-voice-secretary'
/** 秘书会话的工作目录（首次创建时写入会话 header，之后必须一致，否则会 session/conflict）。 */
const SECRETARY_CWD = process.env.VOICE_SECRETARY_CWD ?? homedir()

/**
 * 长任务心跳：被转达的会话连续 35 秒没向用户出声，就往它的收件箱塞一条提醒。
 *
 * 为什么放在插件侧而不是 app.py：语音链路改成「app → 秘书 → 工作会话」以后，
 * app 只认得秘书会话，不知道真正干活的是哪个会话；而插件手里就有 activeSessionId。
 * 提示词（voice_prefix.py 第 3c 条）承诺"连续35秒没出声系统会提醒你"，
 * 评测跑器也复刻了这条 35s 策略——生产端不补上，就是"测试绿、线上没这回事"。
 */
const HB_NUDGE_MS = 35_000
const HB_NUDGE_TEXT =
  '（系统提醒，不是用户的新任务）你已经连续35秒没有向用户的语音播报了。' +
  '请立刻调用 mcp__voice__speak，用不超过30字只讲已经拿到的东西，播完继续手头工作。'

/** 最近一次"用户听得到声音"的时刻：任何一次 speak 发送成功、新一轮语音进来都会刷新。 */
let lastVoiceAt = 0

export const inject = [
  'systemPrompt',
  'webServer',
  'sessionController',
  'workspaceRegistry',
  'sessionQuery',
]

/** 播报纪律（移植自 app/voice_prefix.py，工具名换成 web profile 里的 wire 名）。 */
const PROMPT_TEXT = `[语音交互场景] 用户通过语音和你交流，无法做文字输入，屏幕只偶尔瞄一眼。要求：
1) 自主执行，不要反问澄清，基于合理假设直接做；仅当操作不可逆（删除/覆盖/推送）
或关键信息硬缺失时才提问，且问题要短。
2) 用中文思考和回复。
3) 语音播报工具 mcp__voice__speak 的使用规则（播报是给不看屏幕的人听的；长时间无声会被
用户理解成卡死）：
   a. 开场：任务开始时播一句理解确认，如"收到，这就去调研中国好茶"。
   b. 里程碑（命中就播，不是可选）：不限于成品——骨架写完、核心逻辑跑通、
一个 bug 修完、一类资料查完、阶段切换（如调研转写报告）、失败后换源成功、
关键结论或数字首次确认，都算可播进展。示例："骨架搭好了，现在补交互逻辑"。
只播已发生的事实，不播空承诺；带数字的已走里程要播，如"五个来源查完三个"。
时间锚定兜底：连续干活超 20 秒没出声，就播一句当前进展。
   c. 心跳：系统会在你连续35秒没出声时发提醒，收到提醒立刻用 mcp__voice__speak
播报当前在岗进度，如"还在查，已经有五家媒体的说法"。
   d. 配额下限：跨3次以上工具调用的任务至少播1次中间进度，跨6次以上至少播2次；
不许为了省事跳过里程碑。实在无里程碑可用时，用一句"在岗进度"满足下限。
   e. 出问题：先说卡在哪、需要用户做什么（decision-first），一句说清。
挣扎也要出声：连续两次返工或工具失败后，先播一句简短状态（卡在哪、在怎么修）再继续。
   f. 收尾：播结果+产物位置+是否有下一步需要用户决定；详情和列表让用户看屏幕。
4) 播报节奏：不为每个工具调用播报，也不复述屏幕已展示的内容；非里程碑不播，
同一状态不重复播；进度播报单次不超过30字，纯问答的答案播报可放宽到80字、
一条播完不拆条。过程叙述只走 speak；文字回复只写最终交付详情，
不写"我先…我再…"式过程旁白。
5) 硬性要求（不因任务简单而省略）：收尾播报是标配，答案必须用 mcp__voice__speak
亲口说一遍，不可只写进文字回复——用户不看屏幕。除纯问答外，回合的第一个动作
必须是 speak 开场确认，不得与工具调用合并在同一步。失败/受阻时，失败结论必须用
speak 播报，不许只在文字里说明；播报句内不许出现"我再""我还要""接下来我"这类
衔接下一步的词，征求同意的句子除外（如"等你同意我再删"），说到已完成事实为止。
6) 大文件分段：单文件预计超 150 行时，先写骨架再用 edit 分段补全，不许一次性写完。`

interface WebServerLike {
  register(route: {
    kind: 'exact' | 'prefix'
    path: string
    handler: (req: IncomingMessage, res: ServerResponse) => void | Promise<void>
  }): () => void
}

interface SystemPromptLike {
  section(section: { name: string; order: number; text: string }): () => void
}

/** Host 端 Cordis 事件订阅（waterfall 事件用 `{ prepend: true }` 抢到链首观察）。 */
interface HostOnLike {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (name: string, listener: (...args: any[]) => unknown, options?: { prepend?: boolean }): unknown
}

function sendJson(res: ServerResponse, body: unknown, status = 200): void {
  const data = JSON.stringify(body)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' })
  res.end(data)
}

/** 读 Python 侧落地的 HTTP 端口文件；文件不存在 = 语音运行时未启动。 */
async function voicePort(): Promise<number | null> {
  try {
    const raw = await readFile(join(APP_DIR, 'voice_http.port'), 'utf-8')
    const port = parseInt(raw.trim(), 10)
    return Number.isFinite(port) && port > 0 ? port : null
  } catch {
    return null
  }
}

async function probePython(port: number): Promise<boolean> {
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/state`, {
      signal: AbortSignal.timeout(1500),
    })
    return resp.ok
  } catch {
    return false
  }
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf-8')))
    req.on('error', reject)
  })
}

/** 直连本地 Python 语音运行时让它出声（不经浏览器；不在线就静默）。 */
async function speakViaPython(text: string): Promise<void> {
  try {
    const port = await voicePort()
    if (port === null) return
    await fetch(`http://127.0.0.1:${port}/voice/speak`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text }),
      signal: AbortSignal.timeout(5000),
    })
  } catch {
    /* 语音运行时不在线就静默——官方确认 UI 仍在屏幕上可用 */
  }
}

interface ApprovalRequestLike {
  toolName?: string
}

interface QuestionOptionLike {
  label?: string
}

interface QuestionItemLike {
  question?: string
  header?: string
  options?: readonly (QuestionOptionLike | string)[]
}

interface UserQuestionsRequestLike {
  questions?: readonly QuestionItemLike[]
}

type Next = () => Promise<unknown>

function describeQuestions(req: UserQuestionsRequestLike): string {
  const parts = (req.questions ?? [])
    .map((q) => {
      const head = q.question ?? q.header ?? ''
      const opts = (q.options ?? [])
        .map((o) => (typeof o === 'string' ? o : (o.label ?? '')))
        .filter(Boolean)
        .join('、')
      return opts ? `${head}，选项有：${opts}` : head
    })
    .filter(Boolean)
  return parts.join('；')
}

/**
 * 审批认领后等语音定案的上限；超时即回落官方 UI（用户可在屏幕上手点）。
 *
 * 定 45 秒是因为链路本身很长：播报 → 用户听到并表态 → 遥控器 → ASR 转写 → 投给秘书
 * → 秘书跑一轮 LLM 并调 decide 工具 → HTTP 回到插件。实测一轮约 39 秒，30 秒会误判超时。
 */
const APPROVAL_TIMEOUT_MS = Number(process.env.VOICE_APPROVAL_TIMEOUT_MS ?? 45_000)

/** 待决审批：认领期间由插件持有定案权，秘书的答复与超时回落，先到者胜。 */
interface PendingApproval {
  readonly id: string
  readonly toolName: string
  decide(outcome: 'allowed-once' | 'rejected'): void
}

const pendingApprovals = new Map<string, PendingApproval>()
let latestApprovalId: string | null = null

/** 取待决审批：带 id 精确取；不带则取最近一条（用户口语「允许」时不会报 id）。 */
function findPendingApproval(id?: string): PendingApproval | undefined {
  const key = id ?? latestApprovalId
  return key === null ? undefined : pendingApprovals.get(key)
}

/**
 * HITL 语音：审批改成「认领」——插件在链首持有定案权，等秘书替你签；超时才 `return next()`
 * 回落官方 UI。认领的代价是官方审批卡片在等待期间不会出现（转发监听器在我们下游），
 * 所以超时回落不是可选项，而是安全网。提问仍只观察不认领（选择题交给屏幕更合适）。
 */
function installHitlVoice(c: { on: HostOnLike }): void {
  c.on(
    'approval/request',
    function (req: ApprovalRequestLike, next: Next) {
      const toolName = req.toolName ?? '未知操作'
      const id = randomUUID()
      return new Promise((resolve) => {
        let timer: NodeJS.Timeout | undefined
        /** 定案与回落共用同一出口：先摘掉待决项，保证一条审批只结算一次。 */
        const settle = (value: unknown): void => {
          if (timer !== undefined) clearTimeout(timer)
          if (latestApprovalId === id) latestApprovalId = null
          pendingApprovals.delete(id)
          resolve(value)
        }
        pendingApprovals.set(id, { id, toolName, decide: (outcome) => settle(outcome) })
        latestApprovalId = id
        timer = setTimeout(() => {
          // 超时：先摘掉待决项（迟到的语音答复不再生效），再把定案权整个交回官方链路。
          // 注意不能「await next() 之后再结算」——那样卡片已经显示出来、又因我们先 resolve
          // 而永远等不到结果，屏幕上会留下一个点不动的僵尸审批框。
          settle(next())
          void speakViaPython('没等到你答复，我把确认框放到屏幕上了')
        }, APPROVAL_TIMEOUT_MS)
        void speakViaPython(`需要你确认：${toolName}，说允许或拒绝`)
      })
    },
    { prepend: true },
  )
  // 多选项提问：问题 + 全部选项念出来，用户可直接语音回答
  c.on(
    'user-questions/request',
    function (req: UserQuestionsRequestLike, next: Next) {
      const desc = describeQuestions(req)
      if (desc) void speakViaPython(`需要你回答：${desc}。可以直接语音回答`)
      return next()
    },
    { prepend: true },
  )
}

// ---- 秘书会话：语音的唯一落点 ----

interface SessionSummaryLike {
  sessionId?: string
  updatedAt?: number
  running?: boolean
  blank?: boolean
  cwd?: string
  parentSessionId?: string
  origin?: string
  /** Host 侧投影值；会话标题走其中的 'title' 键（无标题时缺省）。 */
  projections?: { values?: Record<string, unknown> }
}

interface SessionControllerLike {
  create(request: {
    sessionId?: string
    cwd?: string
    /** 与 cwd 二选一：给了它，cwd 取工作区路径，且会话会被挂进该工作区。 */
    workspaceId?: string
  }): Promise<{ sessionId?: string }>
  /** 停掉某会话正在跑的那一轮（保留它收件箱里排队的活）。 */
  cancel(request: { sessionId: string }): Promise<{ accepted?: boolean }>
  list(
    request: Record<string, never>,
    signal: AbortSignal,
  ): Promise<{ items?: readonly SessionSummaryLike[] }>
  prompt(
    request: {
      requestId: string
      sessionId: string
      mode: 'queue' | 'steer'
      content: readonly { type: 'text'; text: string }[]
    },
    signal: AbortSignal,
  ): Promise<unknown>
}

/** 工作区注册表（web profile 的 `workspace` 插件提供）：只用到顺序与路径。 */
interface WorkspaceLike {
  readonly id: string
  readonly path: string
  readonly title: string
}

interface WorkspaceRegistryLike {
  /** 注册表顺序：新建的工作区插到队首，即 UI 分组里最上面那个。 */
  list(): readonly WorkspaceLike[]
}

/** 一段消息里的文本块；user/assistant 消息都是这种块数组。 */
interface TextBlockLike {
  type?: string
  text?: string
}

/** 会话里一条带正文的事件。正文位置两种：user/message 直接在 data 上，assistant/message 包在 message 里。 */
interface SurfaceEventLike {
  type?: string
  data?: {
    message?: { content?: readonly TextBlockLike[] }
    content?: readonly TextBlockLike[]
    /**
     * 消息来源。user/message 有两种：kind 'user' 是真人说的（含秘书转达），
     * kind 'plugin' 是 harness 自己注入的上下文（运行环境快照、文件变更提示…）。
     * 两者正文长得一样，只能靠这个字段区分。
     */
    source?: { kind?: string }
  }
}

/**
 * 会话内容读取（web profile 的 sessionQuery 服务，精确读，不依赖全文检索后端）。
 * 会话日志落盘是 session.v3.jsonl.zstd，秘书没法自己解压读，所以「总结对话」必须走这个接口。
 */
interface SessionQueryLike {
  readSurface(sessionId: string): Promise<{ events?: readonly SurfaceEventLike[] }>
}

/**
 * 秘书职责提醒：随每轮用户语音一起投递。
 * 长会话里秘书容易忘了自己是谁（实测首轮它自称「AI编程助手」），所以职责每轮重申。
 */
const SECRETARY_PREFIX = `[语音秘书场景] 你是用户的语音秘书。用户只说语音、不看屏幕，你是他唯一的对话对象。
你能替用户插手的只有下面五件事；其余问题（问答、闲聊、常识）你自己直接答，不要乱转达。
1) 传话——用户要干活：用 mcp__voice__send_to_session 把用户的原话转达给当前对话。
   不确定传给哪个对话就跟 mcp__voice__list_sessions 对一下；一个都没就先 mcp__voice__new_session。
   转达失败（尤其返回跨工作区）时，对用户必须说清三件事：通道为什么不通、产物最终落在哪里、
   这条需求有没有真的进执行会话；不许默默自己把活干完就当没事。
2) 导航——说「新开一个对话」→ mcp__voice__new_session；
   说「切到写周报那个对话」→ 先 mcp__voice__list_sessions 拿 id，再 mcp__voice__switch_session。
3) 叫停——说「停」「别做了」「先停下」→ mcp__voice__stop_session 停掉当前对话正在跑的那一轮。
   停的只是正在跑的这轮，之前排队的活还会接着跑，别说成「全停了」。
   只有工具明确回「已经让它停了」才可以说「停了」；回「没在跑」只能说成
   「X 当前没有在跑的一轮」——那表示什么都没停，绝不许说成「已停」。
   这是停别人干活，不是让你自己住嘴；停完用 speak 回一句。
4) 汇报——问「那个文件写了什么」「刚才那个对话聊了啥」：文件你自己用读文件的工具看，
   对话用 mcp__voice__read_session 取内容，然后用你自己的话讲结论，不要照念原文，
   不要念代码、路径和长列表。
5) 签字——用户对某个待确认操作表态（「允许/同意/可以」→ allow；「拒绝/不行/算了」→ deny）：
   先调 mcp__voice__list_pending_approvals 确认有没有待决项，有再调 mcp__voice__decide_approval 签字。
   表态含糊、或听不出是同意还是不同意时，不要签，先追问一句。
   拒签后若接下来两轮仍拿不到明确表态，主动再播一次，说清是哪类操作、拖着不动会怎样。
6) 有副作用的动作（停、删、覆盖、转达）——先 mcp__voice__list_sessions 解析出明确 id，再带 id 调用，
   不要依赖「当前对话」这个会被导航随时改写的指针；清单里标「正在忙」才是真的在跑。
7) 听不清、转写乱码——同一句连续两次解析不出来时，不要重复同一问句，
   改成给两三个候选让对方一个字就能选（例如「是要那份报告，还是别的东西？」）。
播报：用户的每个诉求都要用 mcp__voice__speak 说给他听。转达类的回执要短，一句话带过就行
   （例如「已转达」），别复述任务内容——干活的那个对话马上会自己播开场，
   你复述就变成连着两遍一样的话。具体进展也由那个对话播报，你不用替它汇报（第 4 条除外）。
用户这次说的是：
`

/**
 * 转达前缀：这句是秘书替用户说的，开场白已经由秘书播过了。
 * 不写明的话，工作会话会照播报纪律再来一句「收到，我去查…」，用户连着听两遍（实测踩过）。
 */
const RELAY_PREFIX = `[秘书转达] 以下是用户的原话，秘书已经向用户播过回执了。
本轮不要播开场确认，直接开工，从第一个里程碑开始用 mcp__voice__speak 播报，收尾照常。
用户原话：`

/**
 * 把一段话投给某个会话。
 *
 * 先 create 再 prompt：create 对已存在的会话是 adopt（幂等），对冷会话也会自动唤醒，
 * 所以插件不必自己持有 AgentHandle、也不必担心常驻——DSH 没有空闲回收器，建好就不回收。
 */
async function sendToSession(
  sc: SessionControllerLike,
  text: string,
  sessionId: string,
  cwd?: string,
): Promise<void> {
  await sc.create({ sessionId, ...(cwd === undefined ? {} : { cwd }) })
  await sc.prompt(
    {
      requestId: randomUUID(),
      sessionId,
      mode: 'queue',
      content: [{ type: 'text', text }],
    },
    AbortSignal.timeout(10_000),
  )
}

/**
 * 把一条语音文本投给秘书会话。人不再直接对工作 agent 说话，指令都由秘书转达。
 */
async function askSecretary(sc: SessionControllerLike, text: string): Promise<void> {
  await sendToSession(sc, SECRETARY_PREFIX + text, SECRETARY_SESSION_ID, SECRETARY_CWD)
}

// ---- 会话导航：秘书的清单／新建／切换／转达 ----

/** 当前被转达的会话。null = 还没指定，秘书会先问或先开一个。 */
let activeSessionId: string | null = null

/**
 * 待浏览器切过去的目标 + 版本号。
 *
 * 官方没有 Host→客户端的导航通道：sessionController / workspaceController 都没有
 * openSession 这类方法，可转发事件白名单（dsh-api-remotes）里也没有导航事件；
 * 「切会话」这个动作只存在于浏览器侧（uiWorkspace.openSession）。
 * 所以这里只登记意图，由插件的 client 半轮询后执行——Host 不假装能命令浏览器。
 */
let openTarget: string | null = null
let openRevision = 0

function requestOpenInBrowser(sessionId: string): void {
  openTarget = sessionId
  openRevision += 1
}

/** 会话摘要 → 语音可用的形状；标题取 host 投影的 'title'，没有就退回 id。 */
interface SessionView {
  id: string
  title: string
  cwd?: string
  running: boolean
  /** 空壳会话（刚建好还没说过话）：不出现在清单里，但仍可被切到。 */
  blank: boolean
  updatedAt: number
}

function toSessionView(item: SessionSummaryLike): SessionView {
  const id = item.sessionId ?? ''
  const raw = item.projections?.values?.['title']
  return {
    id,
    title: typeof raw === 'string' && raw !== '' ? raw : id,
    cwd: item.cwd,
    running: item.running === true,
    blank: item.blank === true,
    updatedAt: item.updatedAt ?? 0,
  }
}

/**
 * 全部可寻址会话：滤掉秘书自己与子代理会话，保留还没用过的空壳。
 * 切换要用这一份——用户刚说「新开一个对话」又说「切回去」，空壳也得找得到。
 */
async function fetchSessions(sc: SessionControllerLike): Promise<SessionView[]> {
  const { items } = await sc.list({}, AbortSignal.timeout(5000))
  return (items ?? [])
    .filter((i) => typeof i.sessionId === 'string' && i.sessionId !== SECRETARY_SESSION_ID)
    .filter((i) => i.origin !== 'subagent')
    .map(toSessionView)
}

/** 给用户听的候选清单：空壳不算一个能切的对话。 */
async function listSessions(sc: SessionControllerLike): Promise<SessionView[]> {
  return (await fetchSessions(sc)).filter((i) => !i.blank)
}

/**
 * adopt 冷会话时必须带上它自己的 cwd：不带的话 host 会拿进程 cwd 去比对，
 * 直接抛 `session "…" belongs to "…", not "…"`（实测踩过）。
 * 清单里查不到的多半是我们刚建的空壳，其 cwd 就是秘书的工作目录。
 */
async function resolveCwd(sc: SessionControllerLike, sessionId: string): Promise<string> {
  const hit = (await fetchSessions(sc)).find((i) => i.id === sessionId)
  return hit?.cwd ?? SECRETARY_CWD
}

/**
 * 新建会话的落点：最近使用过的那个工作区。
 *
 * 「最近使用」按工作区内最新一次会话活动算——工作区自身的 updatedAt 只记注册表变更
 * （改名、挂会话），不代表用户最近在这儿干过活。一个带会话的工作区都没有时（全新环境），
 * 保持注册表顺序，即取最上面那个。
 */
function pickWorkspace(
  wr: WorkspaceRegistryLike,
  sessions: readonly SessionView[],
): WorkspaceLike | undefined {
  const all = wr.list()
  if (all.length === 0) return undefined
  const norm = (p: string): string => p.replace(/[\\/]+$/, '').toLowerCase()
  const activity = (ws: WorkspaceLike): number =>
    sessions
      .filter((s) => s.cwd !== undefined && norm(s.cwd) === norm(ws.path))
      .reduce((max, s) => Math.max(max, s.updatedAt), 0)
  let best = all[0]
  for (const ws of all) if (activity(ws) > activity(best)) best = ws
  return best
}

/**
 * 新建会话：默认落在最近使用的工作区里（语音场景不逐次问路径）；
 * 注册表为空（全新环境）才退回秘书的工作目录。
 *
 * 用 workspaceId 而不是 cwd：sessionController.create 只有拿到 workspaceId 才会把会话
 * 挂进工作区账户；只给 cwd 的话，即使目录跟工作区一模一样，会话也会落在「未分类」里（实测踩过）。
 */
async function createSession(
  c: { sessionController: SessionControllerLike; workspaceRegistry: WorkspaceRegistryLike },
  cwd?: string,
): Promise<{ sessionId?: string; workspace?: string }> {
  if (cwd !== undefined) return await c.sessionController.create({ cwd })
  const ws = pickWorkspace(c.workspaceRegistry, await fetchSessions(c.sessionController))
  if (ws === undefined) return await c.sessionController.create({ cwd: SECRETARY_CWD })
  return {
    ...(await c.sessionController.create({ workspaceId: ws.id })),
    workspace: ws.title,
  }
}

/** 汇报给用户的会话内容上限：条数与总字数都封顶，免得一次塞爆秘书的上下文。 */
const TRANSCRIPT_MAX_MESSAGES = 40
const TRANSCRIPT_MAX_CHARS = 6000

/** 一条会话消息：谁说的 + 正文。 */
interface TranscriptLine {
  role: 'user' | 'assistant'
  text: string
}

/**
 * 取会话最近若干条带正文的消息，供秘书用自己的话汇报。
 * 只要 user/assistant 两类、只取 text 块——思考、工具调用、工具结果都不进：
 * 用户问的是「聊了什么」，不是执行细节。
 * user/message 还要再过一道 source：harness 注入的运行环境快照也走这个类型，
 * 不过滤的话秘书会把「Current runtime context...」当用户原话念出来。
 */
async function readTranscript(
  sq: SessionQueryLike,
  sessionId: string,
): Promise<TranscriptLine[]> {
  const { events } = await sq.readSurface(sessionId)
  const lines: TranscriptLine[] = []
  for (const ev of events ?? []) {
    if (ev.type !== 'user/message' && ev.type !== 'assistant/message') continue
    if (ev.type === 'user/message' && ev.data?.source?.kind !== 'user') continue
    const blocks = ev.data?.message?.content ?? ev.data?.content ?? []
    const raw = blocks
      .filter((b) => b.type === 'text' && typeof b.text === 'string')
      .map((b) => (b.text ?? '').trim())
      .filter((t) => t !== '')
      .join('\n')
    // 秘书转达时会在原话前面垫一长串样板指令（「本轮不要播开场确认…」），剥掉只留用户原话，
    // 免得秘书汇报时把自己的指令当成对话内容念出来。
    const text = raw.startsWith(RELAY_PREFIX) ? raw.slice(RELAY_PREFIX.length).trim() : raw
    if (text === '') continue
    lines.push({ role: ev.type === 'user/message' ? 'user' : 'assistant', text })
  }
  const tail = lines.slice(-TRANSCRIPT_MAX_MESSAGES)
  let total = tail.reduce((sum, l) => sum + l.text.length, 0)
  while (tail.length > 1 && total > TRANSCRIPT_MAX_CHARS) {
    total -= tail[0].text.length
    tail.shift()
  }
  return tail
}

/**
 * 启用心跳：每 5 秒看一次，被转达的会话在跑且 35 秒没出声就催一句。
 *
 * 只在「有人正在被转达且确实在跑」时催——否则用户在别的会话里打字干活也会被塞提醒。
 * 任何一次 speak 成功 / 新语音进来都会把计时重置，所以正常情况下它不会响。
 */
function startHeartbeat(c: { sessionController: SessionControllerLike }): void {
  lastVoiceAt = Date.now()
  const tick = async (): Promise<void> => {
    try {
      const now = Date.now()
      const target = activeSessionId
      if (target === null) {
        lastVoiceAt = now
        return
      }
      const { items } = await c.sessionController.list({}, AbortSignal.timeout(5000))
      const running = (items ?? []).find((i) => i.sessionId === target)?.running === true
      if (!running) {
        lastVoiceAt = now   // 没在跑就不催，免得空转刷提醒
        return
      }
      if (now - lastVoiceAt < HB_NUDGE_MS) return
      lastVoiceAt = now
      await c.sessionController.prompt(
        {
          requestId: randomUUID(),
          sessionId: target,
          mode: 'queue',
          content: [{ type: 'text', text: HB_NUDGE_TEXT }],
        },
        AbortSignal.timeout(10_000),
      )
    } catch {
      // 心跳失败不能影响主链路：这一轮跳过，下一轮再试
    }
  }
  const timer = setInterval(() => void tick(), 5_000)
  if (typeof timer.unref === 'function') timer.unref()
}

export function apply(ctx: unknown): void {
  const c = ctx as {
    systemPrompt: SystemPromptLike
    webServer: WebServerLike
    on: HostOnLike
    sessionController: SessionControllerLike
    workspaceRegistry: WorkspaceRegistryLike
    sessionQuery: SessionQueryLike
  }

  installHitlVoice(c)

  c.systemPrompt.section({ name: 'voice-broadcast', order: 900, text: PROMPT_TEXT })

  startHeartbeat(c)

  c.webServer.register({
    kind: 'exact',
    path: '/voice/status',
    handler: async (_req, res) => {
      const port = await voicePort()
      const online = port !== null && (await probePython(port))
      sendJson(res, { online, port })
    },
  })

  c.webServer.register({
    kind: 'exact',
    path: '/voice/speak',
    handler: async (req, res) => {
      const port = await voicePort()
      if (port === null) {
        sendJson(res, { ok: false, error: 'voice-runtime-offline' }, 503)
        return
      }
      try {
        const body = await readBody(req)
        const resp = await fetch(`http://127.0.0.1:${port}/voice/speak`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body,
          signal: AbortSignal.timeout(5000),
        })
        if (resp.ok) lastVoiceAt = Date.now()   // 真的出声了，心跳计时重置
        sendJson(res, { ok: resp.ok }, resp.ok ? 200 : 502)
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  // 语音输入：Python 侧转写完成后投到这里，由秘书会话承接并转达给工作会话。
  c.webServer.register({
    kind: 'exact',
    path: '/voice/input',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { text?: string }
        const text = (parsed.text ?? '').trim()
        if (!text) {
          sendJson(res, { ok: false, error: 'empty-text' }, 400)
          return
        }
        lastVoiceAt = Date.now()   // 新一轮语音：先把心跳计时归零，别在秘书思考时误催
        await askSecretary(c.sessionController, text)
        sendJson(res, { ok: true })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  // 待决审批查询：秘书据「允许」前先看看有没有单子在等它签。
  c.webServer.register({
    kind: 'exact',
    path: '/voice/hitl/pending',
    handler: (_req, res) => {
      sendJson(res, {
        pending: [...pendingApprovals.values()].map((p) => ({ id: p.id, toolName: p.toolName })),
      })
    },
  })

  // 审批定案：秘书代替用户签字。allow → allowed-once，其余一律 rejected（默认不授权）。
  c.webServer.register({
    kind: 'exact',
    path: '/voice/hitl/decide',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { decision?: string; id?: string }
        const target = findPendingApproval(parsed.id)
        if (target === undefined) {
          sendJson(res, { ok: false, error: 'no-pending-approval' }, 404)
          return
        }
        const allow = parsed.decision === 'allow'
        target.decide(allow ? 'allowed-once' : 'rejected')
        sendJson(res, {
          ok: true,
          toolName: target.toolName,
          decision: allow ? 'allow' : 'deny',
        })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 500)
      }
    },
  })

  // ---- 会话导航：秘书替用户看清单、开新会话、切会话、把话转达过去 ----

  /** 会话清单：active 是当前被转达的会话，items 是候选（已滤掉秘书与空壳）。 */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions',
    handler: async (_req, res) => {
      try {
        sendJson(res, { active: activeSessionId, items: await listSessions(c.sessionController) })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /**
   * 还有没有会话在跑：语音 App 用它判断回合是否结束。
   * App 看不见会话事件，只能问；这里要算上秘书会话，所以不能用 listSessions（那份把秘书滤掉了）。
   */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/busy',
    handler: async (_req, res) => {
      try {
        const { items } = await c.sessionController.list({}, AbortSignal.timeout(5000))
        sendJson(res, { busy: (items ?? []).some((i) => i.running === true) })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /**
   * 待切会话：插件的浏览器半轮询这里，版本号一变就调官方 uiWorkspace.openSession 切过去。
   * GET 而非 SSE：一个本地回环请求，2 秒一次，省掉长连接的断线重连与多 tab 扇出。
   */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/active',
    handler: (_req, res) => {
      sendJson(res, { sessionId: openTarget, revision: openRevision })
    },
  })

  /** 新建会话并设为当前；落在最近使用的工作区里（语音场景不逐次问路径）。 */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/new',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { cwd?: string }
        const created = await createSession(c, parsed.cwd)
        activeSessionId = created.sessionId ?? null
        if (activeSessionId !== null) requestOpenInBrowser(activeSessionId)
        sendJson(res, {
          ok: true,
          sessionId: activeSessionId,
          ...(created.workspace === undefined ? {} : { workspace: created.workspace }),
        })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /**
   * 叫停：停掉某会话正在跑的那一轮。
   * DSH 只有「打断当前轮次」（sessionController.cancel，保留收件箱里排队的活），
   * 没有销毁会话的动作——所以语音能终止的就是「这一轮」，不是「这个对话」。
   */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/stop',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { sessionId?: string }
        const target = (parsed.sessionId ?? '').trim() || activeSessionId
        if (target === null || target === '') {
          sendJson(res, { ok: false, error: 'no-active-session' }, 409)
          return
        }
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target)
        const { items } = await c.sessionController.list({}, AbortSignal.timeout(5000))
        if ((items ?? []).find((i) => i.sessionId === target)?.running !== true) {
          // stopped:false 是给秘书看的硬信号：什么都没停，不许播成「已停」。
          sendJson(
            res,
            {
              ok: false,
              error: 'not-running',
              stopped: false,
              ...(hit?.title === undefined ? {} : { title: hit.title }),
            },
            409,
          )
          return
        }
        await c.sessionController.cancel({ sessionId: target })
        sendJson(res, {
          ok: true,
          stopped: true,
          sessionId: target,
          ...(hit?.title === undefined ? {} : { title: hit.title }),
        })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /** 读会话内容：秘书据此用自己的话汇报（日志是 zstd，秘书自己读不了）。 */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/read',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { sessionId?: string }
        const target = (parsed.sessionId ?? '').trim() || activeSessionId
        if (target === null || target === '') {
          sendJson(res, { ok: false, error: 'no-active-session' }, 409)
          return
        }
        const lines = await readTranscript(c.sessionQuery, target)
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target)
        sendJson(res, { ok: true, sessionId: target, title: hit?.title ?? target, lines })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /** 切换当前会话：id 必须来自清单，避免秘书听错一个不存在的 id 后把话投进黑洞。 */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/switch',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { sessionId?: string }
        const target = (parsed.sessionId ?? '').trim()
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target)
        if (hit === undefined) {
          sendJson(res, { ok: false, error: 'unknown-session' }, 404)
          return
        }
        activeSessionId = hit.id
        requestOpenInBrowser(hit.id)
        sendJson(res, { ok: true, sessionId: hit.id, title: hit.title })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })

  /** 转达：把用户的话投给当前会话（或指定会话），冷会话由 create 自动唤醒。 */
  c.webServer.register({
    kind: 'exact',
    path: '/voice/sessions/send',
    handler: async (req, res) => {
      if (req.method !== 'POST') {
        sendJson(res, { ok: false, error: 'method-not-allowed' }, 405)
        return
      }
      try {
        const parsed = JSON.parse(await readBody(req)) as { text?: string; sessionId?: string }
        const text = (parsed.text ?? '').trim()
        if (!text) {
          sendJson(res, { ok: false, error: 'empty-text' }, 400)
          return
        }
        const target = parsed.sessionId ?? activeSessionId
        if (target === null) {
          sendJson(res, { ok: false, error: 'no-active-session' }, 409)
          return
        }
        try {
          await sendToSession(
            c.sessionController,
            RELAY_PREFIX + text,
            target,
            await resolveCwd(c.sessionController, target),
          )
        } catch (err) {
          // host 的工作区校验只在转达时抛错，且原文是英文长句，秘书看不出下一步该干什么。
          // 这里翻成结构化错误：目标属于哪个工作区、秘书在哪个工作区，由工具层给出可选动作。
          const message = err instanceof Error ? err.message : String(err)
          const mismatch = /belongs to "([^"]+)",\s*not "([^"]+)"/.exec(message)
          if (mismatch !== null) {
            sendJson(
              res,
              {
                ok: false,
                error: 'workspace-mismatch',
                belongsTo: mismatch[1],
                expected: mismatch[2],
              },
              409,
            )
            return
          }
          throw err
        }
        const hit = (await fetchSessions(c.sessionController)).find((i) => i.id === target)
        lastVoiceAt = Date.now()   // 刚转达=工作会话刚开工，心跳从这一刻起算
        sendJson(res, {
          ok: true,
          sessionId: target,
          ...(hit?.title === undefined ? {} : { title: hit.title }),
        })
      } catch (err) {
        sendJson(res, { ok: false, error: String(err) }, 502)
      }
    },
  })
}
