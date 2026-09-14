// speak 工具（mcp__voice__speak）的自定义卡片：喇叭图标 + 播报文本 + 状态。
// block 为 RunningToolCall（无 kind）或 ToolResultNode（kind='tool-result'），
// 判定逻辑对齐 ui-tool 的 parsedToolCall："kind" in block ? block.call : block。
interface Props {
  callId: string
  toolName: string
  block: unknown
}

interface Parsed {
  text: string
  state: 'running' | 'done' | 'error'
  result: string
}

function parse(block: unknown): Parsed {
  const b = block as Record<string, unknown> | null
  const call = (
    b && 'kind' in b ? (b as { call?: { name: string; argsRaw: string } | null }).call : b
  ) as { name?: string; argsRaw?: string } | null | undefined
  let text = ''
  try {
    const args = JSON.parse(call?.argsRaw ?? '{}') as { text?: unknown }
    text = typeof args.text === 'string' ? args.text : ''
  } catch {
    /* argsRaw 流式中途不是合法 JSON，显示空 */
  }
  const settled = !!b && 'kind' in b
  const isError = settled && (b as { isError?: boolean }).isError === true
  let result = ''
  if (settled) {
    const content = (b as { content?: readonly { type: string; text?: string }[] }).content
    if (content?.length === 1 && content[0]?.type === 'text') result = content[0].text ?? ''
  }
  return { text, state: !settled ? 'running' : isError ? 'error' : 'done', result }
}

export function SpeakCard({ block }: Props) {
  const { text, state, result } = parse(block)
  const color =
    state === 'running' ? 'var(--dsw-alias-label-tertiary, #888)'
    : state === 'error' ? 'var(--dsw-alias-state-error-primary, #e66)'
    : 'var(--dsw-alias-label-secondary, #aaa)'
  const statusText =
    state === 'running' ? '播报中…'
    : state === 'error' ? `播报失败${result ? `：${result}` : ''}`
    : '已播报'
  return (
    <div
      style={{
        display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0,
        margin: '4px 0 4px 4px', fontSize: 13, lineHeight: '24px', color,
      }}
      title={state === 'error' ? result : undefined}
    >
      <svg
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
        style={{ flex: 'none', alignSelf: 'center' }}
      >
        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
        <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
        <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
      </svg>
      <span style={{ flex: 'none' }}>语音播报</span>
      <span
        style={{
          flex: 'auto', minWidth: 0, overflow: 'hidden',
          textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          color: 'var(--dsw-alias-label-tertiary, #888)',
        }}
      >
        {text}
      </span>
      <span style={{ flex: 'none', fontSize: 11, opacity: 0.8 }}>{statusText}</span>
    </div>
  )
}
