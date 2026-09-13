import type { Connections } from '../types'
import { Brand, Icon } from '../ui'

export default function Ready({ conn, onOpen }: { conn: Connections | null; onOpen: () => void }) {
  const setup = conn?.setup
  const rows = [
    { icon: Icon.github, label: 'Watching', value: setup?.repo, on: Boolean(conn?.github.connected && setup?.repo) },
    {
      icon: Icon.linear,
      label: 'Issues in',
      value: setup?.team ? (setup.team.name ?? setup.team.key ?? setup.team.id) : null,
      on: Boolean(conn?.linear.connected && setup?.team),
    },
    { icon: Icon.slack, label: 'Threads in', value: setup?.channel?.name ?? setup?.channel?.id, on: Boolean(conn?.slack.connected && setup?.channel) },
  ]
  return (
    <div className="ready">
      <div className="ready-card">
        <Brand size={32} />
        <h1 className="serif ready-title">You're set.</h1>
        <div className="ready-rows">
          {rows.map((r) => (
            <div className={`ready-row ${r.on ? 'is-on' : ''}`} key={r.label}>
              <span className="ready-icon">{r.icon}</span>
              <span className="muted">{r.label}</span>
              <b className="mono">{r.on ? r.value : 'not connected'}</b>
              <span className="ready-state">{r.on ? Icon.check : null}</span>
            </div>
          ))}
        </div>
        <button className="btn btn-primary btn-lg" onClick={onOpen}>
          Open the dashboard {Icon.chevron}
        </button>
      </div>
    </div>
  )
}
