import { useEffect, useState } from 'react'
import { api } from '../bridge'
import type { AppName, Connections, ToolsStatus } from '../types'
import { APP_NAMES, APP_ORDER, Brand, CheckRow, Icon } from '../ui'
import type { ConnectStep } from './Connect'

interface Props {
  conn: Connections | null
  refresh: (force?: boolean) => Promise<Connections | null>
  onReconnect: (step: ConnectStep) => void
  onBack: () => void
  onLoggedOut: () => void
}

export default function Settings({ conn, refresh, onReconnect, onBack, onLoggedOut }: Props) {
  const [busy, setBusy] = useState<AppName | 'all' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [tools, setTools] = useState<ToolsStatus | null>(null)
  const setup = conn?.setup
  const where: Record<AppName, string | null | undefined> = {
    github: setup?.repo,
    linear: setup?.team ? (setup.team.name ?? setup.team.key ?? setup.team.id) : null,
    slack: setup?.channel?.name ?? setup?.channel?.id,
  }

  const checkTools = () => void api.tools().then(setTools, () => setTools(null))
  useEffect(checkTools, [])

  const disconnect = async (app: AppName) => {
    setBusy(app)
    try {
      await api.disconnect(app)
      await refresh(true)
    } finally {
      setBusy(null)
    }
  }

  const logout = async () => {
    setBusy('all')
    try {
      await api.logout()
      await refresh(true)
      onLoggedOut()
    } finally {
      setBusy(null)
    }
  }

  const missing = tools?.tools.filter((t) => t.required && !t.ok).map((t) => t.name) ?? []

  return (
    <div className="settings">
      <header className="settings-head">
        <button className="btn btn-ghost" onClick={onBack}>
          <span className="flip">{Icon.chevron}</span> Back
        </button>
        <Brand size={24} />
        {confirming ? (
          <div className="settings-confirm">
            <span className="muted">Log out of GitHub, Linear and Slack? Tokens are removed from this computer.</span>
            <button className="btn btn-danger" disabled={busy !== null} onClick={() => void logout()}>
              Log out
            </button>
            <button className="btn btn-ghost" disabled={busy !== null} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <div className="card-actions">
            <button
              className="btn"
              disabled={busy !== null}
              onClick={async () => {
                setBusy('all')
                checkTools()
                await refresh(true)
                setBusy(null)
              }}
            >
              Check again
            </button>
            <button className="btn btn-ghost btn-danger-text" disabled={busy !== null} onClick={() => setConfirming(true)}>
              Log out
            </button>
          </div>
        )}
      </header>

      <About />

      <h2 className="serif settings-title">Connections</h2>
      <p className="muted settings-lead">Each app is checked live against its service. Tokens stay in .env on this computer.</p>
      <div className="settings-grid">
        {APP_ORDER.map((app) => {
          const check = conn?.[app] ?? null
          return (
            <section className="card settings-card" key={app}>
              <div className="card-title-row">
                <span className="step-app">{Icon[app]}</span>
                <h3>{APP_NAMES[app]}</h3>
              </div>
              <CheckRow check={busy === app || busy === 'all' ? null : check} pending={busy === app || busy === 'all'} />
              <div className="kv">
                <span className="muted">{app === 'github' ? 'Watching' : app === 'linear' ? 'Team' : 'Channel'}</span>
                <b className="mono">{where[app] || 'not set'}</b>
              </div>
              <div className="card-actions">
                <button className="btn" onClick={() => onReconnect(app)}>
                  {check?.connected ? 'Change' : 'Connect'}
                </button>
                {check?.connected && (
                  <button className="btn btn-ghost" disabled={busy !== null} onClick={() => void disconnect(app)}>
                    Disconnect
                  </button>
                )}
              </div>
            </section>
          )
        })}
      </div>

      <section className="card settings-local">
        <div className="card-title-row">
          <h3>This computer</h3>
        </div>
        <div className="kv">
          <span className="muted">Chip tools</span>
          <b className={tools && !tools.ready ? 'is-bad' : ''}>
            {tools === null ? 'checking' : tools.ready ? tools.tools.filter((t) => t.ok).map((t) => t.name).join(', ') : `missing ${missing.join(', ')}`}
          </b>
        </div>
        <CheckRow check={conn?.anthropic ?? null} what="Claude API key" />
        <div className="card-actions">
          <button className="btn" onClick={() => onReconnect('tools')}>
            Check tools and key
          </button>
        </div>
      </section>

      {conn?.watcher.state === 'error' && (
        <p className="note is-bad settings-watcher">The CI watcher last failed with: {conn.watcher.error}</p>
      )}
    </div>
  )
}

const STAGES = ['Reproduce', 'Rule out', 'Confirm', 'Fix']

/** The pipeline drawn as a chip: a red CI run comes in on the left, the proven answer leaves on the right. */
function ChipDiagram() {
  const pinsY = [70, 110, 150]
  return (
    <svg className="about-chip" viewBox="0 0 560 220" role="img" aria-label="CULPRIT drawn as a chip: a red pull request goes in, four checked stages run, the answer goes out to GitHub, Linear and Slack">
      {[150, 210, 270, 330, 390].map((x) => (
        <g key={x} className="chip-pin">
          <rect x={x} y={14} width={10} height={22} rx={2} />
          <rect x={x} y={184} width={10} height={22} rx={2} />
        </g>
      ))}
      <rect className="chip-body" x={110} y={36} width={320} height={148} rx={14} />
      <text className="chip-label" x={270} y={60} textAnchor="middle">
        CULPRIT
      </text>
      {STAGES.map((stage, i) => (
        <g key={stage}>
          <rect className="chip-stage" x={128 + i * 76} y={88} width={64} height={44} rx={7} />
          <text className="chip-stage-text" x={160 + i * 76} y={114} textAnchor="middle">
            {stage}
          </text>
          {i < STAGES.length - 1 && <path className="chip-trace" d={`M${192 + i * 76} 110 H${204 + i * 76}`} />}
        </g>
      ))}
      <text className="chip-sub" x={270} y={162} textAnchor="middle">
        every step checked by the simulator
      </text>
      <path className="chip-trace is-in" d="M34 110 H110" />
      <circle className="chip-dot is-red" cx={34} cy={110} r={6} />
      <text className="chip-io" x={34} y={92} textAnchor="middle">
        CI red
      </text>
      {['GitHub', 'Linear', 'Slack'].map((app, i) => (
        <g key={app}>
          <path className="chip-trace" d={`M430 110 C455 110 455 ${pinsY[i]} 480 ${pinsY[i]}`} />
          <circle className="chip-dot" cx={484} cy={pinsY[i]} r={4} />
          <text className="chip-io is-right" x={494} y={pinsY[i] + 4}>
            {app}
          </text>
        </g>
      ))}
    </svg>
  )
}

function About() {
  return (
    <section className="about">
      <div className="about-copy">
        <div className="eyebrow">About CULPRIT</div>
        <h2 className="serif about-title">A debugging agent for chip teams.</h2>
        <p className="about-lead">
          When a pull request turns the simulation tests red, CULPRIT reproduces the failure, proves which edit and which line caused it by
          re-running the simulator, writes a fix that keeps the author's change, and posts the answer to GitHub, Linear and Slack.
        </p>
        <ul className="about-points">
          <li>
            <b>Hours back.</b> Debug is the largest share of a verification engineer's time (47%, Wilson Research Group). CULPRIT does the first
            pass: which edit, which line, and a fix that passes.
          </li>
          <li>
            <b>Nothing unproven reaches the team.</b> A culprit counts only when undoing it makes the regression pass, and a fix only when every
            test passes. Anything else is labelled a proposal.
          </li>
          <li>
            <b>Stays in your tools.</b> A one-click suggestion on the pull request line, one Linear issue, one Slack thread, closed when CI is
            green.
          </li>
        </ul>
      </div>
      <div className="about-side">
        <ChipDiagram />
        <div className="about-stats">
          <div>
            <b>58/58</b>
            <span>culprit edits found</span>
          </div>
          <div>
            <b>58/58</b>
            <span>fixes pass every test</span>
          </div>
          <div>
            <b>0/65</b>
            <span>wrong blames</span>
          </div>
          <div>
            <b>30/30</b>
            <span>same grades over 3 runs</span>
          </div>
        </div>
        <p className="about-source">Measured on 65 broken pull requests on PicoRV32 (evals/results/REPORT.md).</p>
      </div>
    </section>
  )
}
