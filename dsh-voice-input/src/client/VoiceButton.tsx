import { useEffect, useState } from 'react'

type Phase = 'checking' | 'online' | 'offline'

/**
 * sidebar 底部的语音状态灯。
 *
 * 为什么只是个「灯」而不是录音按钮：录音发生在桌面端——按住蓝牙遥控器的语音键，
 * BLE → app/link.py → 转写 → POST /voice/input → 秘书会话。浏览器这一侧拿不到音频，
 * 也没必要拿。所以这里只回答一个问题：本机的语音运行时在线没有（没在线就是没启动
 * 启动.bat，或者它挂到别的目录去了）。
 *
 * 探活链路：浏览器 → Host 半 /voice/status → 读 app/voice_http.port → 打 Python /api/state。
 */
export function VoiceButton() {
  const [phase, setPhase] = useState<Phase>('checking')

  useEffect(() => {
    let alive = true
    const poll = async (): Promise<void> => {
      try {
        const resp = await fetch('/voice/status')
        const data = (await resp.json()) as { online?: boolean }
        if (alive) setPhase(data.online === true ? 'online' : 'offline')
      } catch {
        // Host 半没挂上（插件没加载）：也算离线
        if (alive) setPhase('offline')
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 5000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  const color = phase === 'online' ? '#3fb950' : phase === 'offline' ? '#8b949e' : '#d29922'
  const label = phase === 'online' ? '语音在线' : phase === 'offline' ? '语音离线' : '语音…'
  const title =
    phase === 'online'
      ? '按住遥控器语音键说话，松手即发送给秘书'
      : '语音运行时未启动：双击仓库根目录的「启动.bat」'

  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '4px 8px', fontSize: 12, fontFamily: 'sans-serif',
        color: 'inherit', opacity: phase === 'online' ? 1 : 0.6, cursor: 'default',
      }}
    >
      <span
        style={{
          width: 7, height: 7, borderRadius: '50%', background: color,
          boxShadow: phase === 'online' ? `0 0 5px ${color}` : 'none', flex: 'none',
        }}
      />
      {label}
    </span>
  )
}
