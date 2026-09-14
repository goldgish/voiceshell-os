// Client entry（浏览器端）。
// 关键决策（2026-09-10）：slot 从 conversation.session.header.utilities（session scope，
// 无活跃会话时整条 header 不渲染）迁到 sidebar.footer.action（root scope、全局常驻，
// 官方 cordis 面板同款，dsh-cordis-client-runner CLIENT_SLOT_API 标注 risk=none 纯增量）。
//
// 浏览器侧只做两件事：一个语音在线状态灯（VoiceButton），一个 speak 工具卡片（SpeakCard）。
// 录音与转写全在桌面端 app/ 里发生（遥控器 → BLE → ASR → POST /voice/input），
// 这里不接音频、也不再连本地 WebSocket。
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import { VoiceButton } from './VoiceButton'
import { SpeakCard } from './SpeakCard'

const NS = 'voice-input'

// cordis 服务依赖（ctx.inject，等齐后 apply 才执行）：
//   slots        —— 注册 sidebar 状态灯 / speak toolview
//   sessions     —— 会话列表（dsh-api-session-controller，root 提供）：轮询等待目标会话出现
//   uiWorkspace  —— openSession 官方动词（dsh-client-ui-workspace）：跟随语音切会话
//   workspace    —— 工作区服务（dsh-api-workspace-controller）
//   locale       —— slot 注册的 i18n 命名空间
// 注：HITL 语音提醒（审批/提问）在 Host 半做，不在浏览器——浏览器侧 `remote.$on` 监听器
// 排在官方 answerer 之后会被其 waterfall 否决，详见 src/index.ts 顶部说明。
export const inject = ['slots', 'sessions', 'workspaces', 'uiWorkspace', 'locale']

export function apply(ctx: ClientContext): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const c = ctx as any

  /**
   * 跟随语音切会话：Host 半没有命令浏览器的通道（官方只把「切会话」给了浏览器侧的
   * uiWorkspace.openSession），所以那边只登记「待切目标 + 版本号」，这里轮询后执行。
   * 首次轮询只记基线不动页面——刷新时不该被上一次语音开过的会话拽走。
   */
  ctx.effect(() => {
    let seen = -1
    let pending: string | null = null
    const timer = setInterval(() => {
      void (async () => {
        try {
          const resp = await fetch('/voice/sessions/active')
          const data = (await resp.json()) as { sessionId?: string | null; revision?: number }
          const revision = typeof data.revision === 'number' ? data.revision : 0
          if (seen < 0) {
            seen = revision
            return
          }
          if (revision !== seen) {
            seen = revision
            pending = data.sessionId ?? null
          }
          if (pending === null) return
          const snap = c.sessions.list.getSnapshot()
          // 新会话的行可能还没到本地列表，等下轮再切（open 对未知 id 会抛）
          if (snap.byId[pending] === undefined) return
          if (snap.current !== pending) c.uiWorkspace.openSession(pending)
          pending = null
        } catch {
          // Host 不在线（插件没挂 / 实例没起）：静默跳过，下轮再试
        }
      })()
    }, 2000)
    return () => clearInterval(timer)
  })

  // 语音在线状态灯：sidebar.footer.action（root scope，常驻）。
  // 组件无需 inject 任何 props——状态自己轮询 Host 半的 /voice/status。
  c.slots.inject('sidebar.footer.action', () =>
    c.slots.register(
      { name: 'sidebar.footer.action', id: 'voice-input', order: 200, locale: NS },
      VoiceButton,
    ),
  )

  // speak 工具卡片：keyed toolview，key 为 wire 工具名（dsh-mcp-client 命名
  // 契约 mcp__<serverName>__<rawName>，serverName=voice 见 profiles/web/cordis.patch.yml）。
  // 未命中 key 回退官方 GenericToolCard，本注册是纯增量。
  c.slots.inject('tool.call.toolview', () =>
    c.slots.register(
      { name: 'tool.call.toolview', key: 'mcp__voice__speak', locale: NS },
      SpeakCard,
    ),
  )
}
