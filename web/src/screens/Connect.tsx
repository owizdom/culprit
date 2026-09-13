import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, sleep } from '../bridge'
import type { AppName, ChannelCheck, Check, Connections, LinearTeam, ToolRow, ToolsStatus } from '../types'
import { APP_NAMES, APP_ORDER, Brand, CheckRow, Icon, Spinner } from '../ui'
import RepoPicker from './RepoPicker'

type Refresh = (force?: boolean) => Promise<Connections | null>

export type ConnectStep = 'tools' | AppName
const STEP_ORDER: ConnectStep[] = ['tools', ...APP_ORDER]
const STEP_NAMES: Record<ConnectStep, string> = { tools: 'Chip tools', ...APP_NAMES }

const CHIP_ICON = (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
    <rect x="6" y="6" width="12" height="12" rx="2" />
    <path d="M9 2v3M12 2v3M15 2v3M9 19v3M12 19v3M15 19v3M2 9h3M2 12h3M2 15h3M19 9h3M19 15h3" />
    <path d="M19 12h3" stroke="var(--red)" />
  </svg>
)

interface Props {
  conn: Connections | null
  step: ConnectStep
  returnTo: 'ready' | 'settings'
  refresh: Refresh
  onStep: (step: ConnectStep) => void
  onFinish: () => void
}

export default function Connect(p: Props) {
  const [toolsReady, setToolsReady] = useState<boolean | null>(null)
  useEffect(() => {
    void api.tools().then((s) => setToolsReady(s.ready), () => setToolsReady(false))
  }, [])
  const index = STEP_ORDER.indexOf(p.step)
  const last = STEP_ORDER.length - 1
  const next = () => (p.returnTo === 'settings' || index === last ? p.onFinish() : p.onStep(STEP_ORDER[index + 1]))
  const stepProps = { conn: p.conn, refresh: p.refresh, onContinue: next, inSettings: p.returnTo === 'settings', final: index === last }

  const sub = (step: ConnectStep) => {
    if (step === 'tools') return toolsReady === null ? 'Checking' : toolsReady ? 'Ready' : 'Needs setup'
    const check = p.conn?.[step]
    return check?.connected ? check.identity : step === 'github' ? 'Required' : 'Optional'
  }
  const done = (step: ConnectStep) => (step === 'tools' ? toolsReady === true : Boolean(p.conn?.[step]?.connected))

  return (
    <div className="connect">
      <aside className="connect-side">
        <Brand size={28} />
        <h2 className="serif connect-title">Set up CULPRIT</h2>
        <p className="connect-intro">Every tool and connection is checked live before anything is saved.</p>
        <ol className="stepper">
          {STEP_ORDER.map((step, i) => (
            <li key={step}>
              <button className={`stepper-item ${step === p.step ? 'is-current' : ''} ${done(step) ? 'is-done' : ''}`} onClick={() => p.onStep(step)}>
                <span className="stepper-num">{done(step) ? Icon.check : i + 1}</span>
                <span className="stepper-text">
                  <b>{STEP_NAMES[step]}</b>
                  <span>{sub(step)}</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
        <p className="connect-foot">Tokens stay in .env on this computer.</p>
      </aside>
      <main className="connect-main">
        {p.step === 'tools' && <ToolsStep key="tools" {...stepProps} onReady={setToolsReady} />}
        {p.step === 'github' && <GitHubStep key="github" {...stepProps} />}
        {p.step === 'linear' && <LinearStep key="linear" {...stepProps} />}
        {p.step === 'slack' && <SlackStep key="slack" {...stepProps} />}
      </main>
    </div>
  )
}

interface StepProps {
  conn: Connections | null
  refresh: Refresh
  onContinue: () => void
  inSettings: boolean
  final: boolean
}

const actionLabel = (inSettings: boolean, final: boolean) => (inSettings ? 'Save' : final ? 'Finish' : 'Continue')

/** Your own attempt always shows; the saved state only once there is something to say (connected, or a stored token that stopped working). */
const shownCheck = (attempt: Check | null, saved: Check | undefined) => attempt ?? (saved && (saved.connected || saved.token_hint) ? saved : null)

function StepHead({ app, title, children }: { app: ConnectStep; title: string; children: ReactNode }) {
  return (
    <header className="step-head">
      <span className="step-app">{app === 'tools' ? CHIP_ICON : Icon[app]}</span>
      <div>
        <div className="eyebrow">
          Step {STEP_ORDER.indexOf(app) + 1} of {STEP_ORDER.length}
        </div>
        <h1 className="serif step-title">{title}</h1>
        <p className="step-lead">{children}</p>
      </div>
    </header>
  )
}

function useAlive() {
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])
  return alive
}

/* ---------- Chip tools ---------- */

function ToolLine({ row, onBuild }: { row: ToolRow; onBuild: () => void }) {
  const building = row.build?.state === 'building'
  const command = !row.ok && row.required && row.fix && !row.fix.includes('http')
  return (
    <div className={`tool ${row.ok ? 'is-ok' : row.required ? 'is-bad' : 'is-optional'}`}>
      <span className="tool-mark">{row.ok ? Icon.check : row.required ? Icon.cross : '–'}</span>
      <span className="tool-name">
        {row.name}
        {!row.required && <span className="path-tag">optional</span>}
      </span>
      <span className="tool-detail">
        {row.ok ? <span className="mono">{row.version}</span> : command ? <code className="tool-fix">{row.fix}</code> : row.fix}
        {!row.required && <span className="muted"> {row.note}</span>}
        {row.build?.state === 'failed' && <span className="is-bad"> The toolchain build failed.</span>}
      </span>
      {row.ok && row.image === false && (
        <button className="btn btn-sm" disabled={building} onClick={onBuild}>
          {building ? (
            <>
              <Spinner /> Building
            </>
          ) : (
            'Build firmware toolchain'
          )}
        </button>
      )}
    </div>
  )
}

function ToolsStep({ conn, refresh, onContinue, inSettings, onReady }: StepProps & { onReady: (ready: boolean) => void }) {
  const [status, setStatus] = useState<ToolsStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState('')
  const [keyCheck, setKeyCheck] = useState<Check | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const s = await api.tools()
      setStatus(s)
      onReady(s.ready)
    } finally {
      setBusy(false)
    }
  }, [onReady])

  useEffect(() => {
    void load()
  }, [load])

  const building = Boolean(status?.tools.some((t) => t.build?.state === 'building'))
  useEffect(() => {
    if (!building) return
    const timer = setInterval(() => void load(), 3000)
    return () => clearInterval(timer)
  }, [building, load])

  const saveKey = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const c = await api.saveAnthropic(key)
      setKeyCheck(c)
      if (c.connected) {
        setKey('')
        void refresh(true)
      }
    } finally {
      setBusy(false)
    }
  }
  const keyOk = Boolean((keyCheck ?? conn?.anthropic)?.connected)

  return (
    <section className="step">
      <StepHead app="tools" title="Check your chip tools">
        CULPRIT runs your regression on this computer with Icarus Verilog and Yosys, and asks Claude only for fixes the simulator then checks.
      </StepHead>
      <div className="tool-list">
        {status === null ? <CheckRow check={null} pending /> : status.tools.map((row) => (
          <ToolLine key={row.name} row={row} onBuild={() => void api.buildToolchain().then(load)} />
        ))}
      </div>
      <form className="field" onSubmit={(e) => void saveKey(e)}>
        <label className="field-label" htmlFor="anthropic-key">
          Claude API key <span className="muted">for fixes that need the model</span>
        </label>
        {!keyOk && (
          <div className="field-row">
            <input id="anthropic-key" className="input mono" type="password" placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn" disabled={busy || !key.trim()}>
              Check
            </button>
          </div>
        )}
        <CheckRow check={shownCheck(keyCheck, conn?.anthropic)} what="Claude API key" />
      </form>
      <div className="step-actions">
        <button className="btn btn-ghost" disabled={busy} onClick={() => void load()}>
          Check again
        </button>
        <button className="btn btn-primary" disabled={!status?.ready} onClick={onContinue}>
          {inSettings ? 'Done' : 'Continue'} {Icon.chevron}
        </button>
      </div>
    </section>
  )
}

/* ---------- GitHub ---------- */

function GitHubStep({ conn, refresh, onContinue, inSettings }: StepProps) {
  const alive = useAlive()
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [token, setToken] = useState('')
  const [device, setDevice] = useState<{ code: string; uri: string } | null>(null)
  const [deviceError, setDeviceError] = useState<string | null>(null)
  const current = check ?? conn?.github ?? null
  const connected = Boolean(current?.connected)

  const connectWith = async (label: string, fn: () => Promise<Check>) => {
    setBusy(label)
    setDeviceError(null)
    try {
      const c = await fn()
      setCheck(c)
      if (c.connected) void refresh(true)
    } finally {
      setBusy(null)
    }
  }

  const signIn = async () => {
    setBusy('device')
    setDeviceError(null)
    const start = await api.githubDeviceStart()
    if ('error' in start) {
      setDeviceError(start.error)
      setBusy(null)
      return
    }
    setDevice({ code: start.user_code, uri: start.verification_uri })
    void api.openUrl(start.verification_uri)
    let wait = start.interval
    while (alive.current) {
      await sleep(wait * 1000)
      const r = await api.githubDevicePoll()
      if (r.state === 'pending') {
        wait = r.interval ?? wait
        continue
      }
      setDevice(null)
      setBusy(null)
      if (r.state === 'done' && r.check) {
        setCheck(r.check)
        if (r.check.connected) void refresh(true)
      } else {
        setDeviceError(
          r.state === 'denied' ? 'Sign in was cancelled on GitHub.' : r.state === 'expired' ? 'The code expired. Start again.' : (r.error ?? 'Sign in failed.'),
        )
      }
      return
    }
  }

  const saveRepo = async (name: string) => {
    const r = await api.saveSetup({ repo: name })
    if (r.ok) {
      await refresh(true)
      onContinue()
    }
  }

  return (
    <section className="step">
      <StepHead app="github" title="Connect GitHub">
        CULPRIT watches one repository's CI and suggests the fix on the exact line. It never pushes, merges or approves.
      </StepHead>

      {!connected && (
        <div className="options">
          <button className="btn btn-primary" disabled={Boolean(busy)} onClick={() => void signIn()}>
            {Icon.github} Sign in with GitHub
          </button>
          <button className="btn" disabled={Boolean(busy)} onClick={() => void connectWith('cli', api.githubUseCli)}>
            Use my GitHub CLI login
          </button>
          <div className="or">
            <span>or paste a token</span>
          </div>
          <form
            className="field-row"
            onSubmit={(e) => {
              e.preventDefault()
              void connectWith('token', () => api.saveToken('github', token))
            }}
          >
            <input className="input mono" type="password" placeholder="ghp_… or github_pat_…" value={token} onChange={(e) => setToken(e.target.value)} />
            <button className="btn" disabled={Boolean(busy) || !token.trim()}>
              Connect
            </button>
          </form>
        </div>
      )}

      {device && (
        <div className="device">
          <div className="eyebrow">Enter this code on GitHub</div>
          <div className="device-code mono">{device.code}</div>
          <button className="link" onClick={() => void api.openUrl(device.uri)}>
            Open {device.uri.replace('https://', '')} {Icon.arrow}
          </button>
          <div className="device-wait">
            <Spinner /> Waiting for you to approve CULPRIT on GitHub
          </div>
        </div>
      )}
      {deviceError && (
        <div className="check is-bad">
          <span className="check-icon">{Icon.cross}</span>
          <b>{deviceError}</b>
        </div>
      )}

      <CheckRow check={busy === 'cli' || busy === 'token' ? null : shownCheck(check, conn?.github)} pending={busy === 'cli' || busy === 'token'} what="Signed in as" />

      {connected && (
        <div className="field">
          <div className="field-label">Repository to watch</div>
          <RepoPicker current={conn?.setup.repo ?? null} saveLabel={inSettings ? 'Save' : 'Continue'} onSaved={saveRepo} />
        </div>
      )}

      {connected && (
        <div className="step-actions">
          <button className="btn btn-ghost" disabled={Boolean(busy)} onClick={() => void connectWith('disconnect', () => api.disconnect('github'))}>
            Use a different account
          </button>
          {conn?.setup.repo && (
            <button className="btn" onClick={onContinue}>
              Keep {conn.setup.repo}
            </button>
          )}
        </div>
      )}
    </section>
  )
}

/* ---------- Linear ---------- */

function LinearStep({ conn, refresh, onContinue, inSettings, final }: StepProps) {
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState('')
  const [teams, setTeams] = useState<LinearTeam[] | null>(null)
  const [team, setTeam] = useState<string | null>(conn?.setup.team?.id ?? null)
  const [error, setError] = useState<string | null>(null)
  const current = check ?? conn?.linear ?? null
  const connected = Boolean(current?.connected)

  useEffect(() => {
    if (!connected) return
    void api.linearTeams().then(
      (list) => {
        setTeams(list)
        setTeam((t) => t ?? (list.length === 1 ? list[0].id : null))
      },
      () => setTeams([]),
    )
  }, [connected])

  const connect = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const c = await api.saveToken('linear', key)
      setCheck(c)
      if (c.connected) {
        setTeams(null)
        void refresh(true)
      }
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!team) return
    setBusy(true)
    setError(null)
    try {
      const r = await api.saveSetup({ team_id: team })
      if (!r.ok) return setError(r.error ?? 'Could not save the team.')
      await refresh(true)
      onContinue()
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="step">
      <StepHead app="linear" title="Connect Linear">
        One issue per broken pull request, with the evidence and the fix. Moved to Done when CI is green.
      </StepHead>
      {!connected && (
        <form className="field" onSubmit={(e) => void connect(e)}>
          <label className="field-label" htmlFor="linear-key">
            Personal API key
          </label>
          <div className="field-row">
            <input id="linear-key" className="input mono" type="password" placeholder="lin_api_…" value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || !key.trim()}>
              Connect
            </button>
          </div>
          <p className="note">In Linear: Settings, then Security &amp; access, then Personal API keys.</p>
        </form>
      )}
      <CheckRow check={busy && !connected ? null : shownCheck(check, conn?.linear)} pending={busy && !connected} />

      {connected && (
        <div className="field">
          <div className="field-label">Team for CULPRIT's issues</div>
          {teams === null ? (
            <CheckRow check={null} pending />
          ) : (
            <div className="choices">
              {teams.map((t) => (
                <button key={t.id} className={`choice ${team === t.id ? 'is-on' : ''}`} onClick={() => setTeam(t.id)}>
                  <span className="choice-key mono">{t.key}</span>
                  <span>{t.name}</span>
                  {team === t.id && <span className="choice-check">{Icon.check}</span>}
                </button>
              ))}
            </div>
          )}
          {error && <p className="note is-bad">{error}</p>}
        </div>
      )}

      <div className="step-actions">
        {!inSettings && (
          <button className="btn btn-ghost" onClick={onContinue}>
            Connect later
          </button>
        )}
        <button className="btn btn-primary" disabled={!connected || !team || busy} onClick={() => void save()}>
          {actionLabel(inSettings, final)} {Icon.chevron}
        </button>
      </div>
    </section>
  )
}

/* ---------- Slack ---------- */

function SlackStep({ conn, refresh, onContinue, inSettings, final }: StepProps) {
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState('')
  const [channel, setChannel] = useState(conn?.setup.channel?.id ?? '')
  const [channelCheck, setChannelCheck] = useState<ChannelCheck | null>(null)
  const current = check ?? conn?.slack ?? null
  const connected = Boolean(current?.connected)

  const connect = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const c = await api.saveToken('slack', token)
      setCheck(c)
      if (c.connected) void refresh(true)
    } finally {
      setBusy(false)
    }
  }

  const probe = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      setChannelCheck(await api.slackChannel(channel))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    if (!channelCheck?.ok || !channelCheck.id) return
    setBusy(true)
    try {
      const r = await api.saveSetup({ channel: channelCheck.id })
      if (r.ok) {
        await refresh(true)
        onContinue()
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="step">
      <StepHead app="slack" title="Connect Slack">
        One thread per broken pull request in your channel, updated as the status changes.
      </StepHead>
      {!connected && (
        <>
          <ol className="setup-list">
            <li>
              <span>
                Create the Slack app: choose <b>Create New App</b>, then <b>From a manifest</b>, and paste the file <span className="mono">apps/slack_manifest.yaml</span>.
              </span>
              <button className="link" onClick={() => void api.openUrl('https://api.slack.com/apps')}>
                Open api.slack.com/apps {Icon.arrow}
              </button>
            </li>
            <li>
              <span>
                Install it to your workspace, then copy the <b>Bot User OAuth Token</b> from OAuth &amp; Permissions. It starts with <span className="mono">xoxb-</span>.
              </span>
            </li>
          </ol>
          <form className="field-row" onSubmit={(e) => void connect(e)}>
            <input className="input mono" type="password" placeholder="xoxb-…" value={token} onChange={(e) => setToken(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || !token.trim()}>
              Connect
            </button>
          </form>
        </>
      )}
      <CheckRow check={busy && !connected ? null : shownCheck(check, conn?.slack)} pending={busy && !connected} what="Connected as" />

      {connected && (
        <form className="field" onSubmit={(e) => void probe(e)}>
          <label className="field-label" htmlFor="channel">
            Channel
          </label>
          <div className="field-row">
            <input
              id="channel"
              className="input mono"
              value={channel}
              placeholder="Paste the channel link (channel name, then Copy link)"
              onChange={(e) => {
                setChannel(e.target.value)
                setChannelCheck(null)
              }}
            />
            <button className="btn" disabled={busy || !channel.trim()}>
              Check
            </button>
          </div>
          <p className="note">
            First invite the bot: in the channel, type <span className="mono">/invite @culprit</span>.
          </p>
          {channelCheck &&
            (channelCheck.ok ? (
              <div className="check is-ok">
                <span className="check-icon">{Icon.check}</span>
                <span>
                  The bot can post in <b className="mono">{channelCheck.id}</b>
                </span>
              </div>
            ) : (
              <div className="check is-bad">
                <span className="check-icon">{Icon.cross}</span>
                <span>
                  <b>{channelCheck.error}</b>
                  {channelCheck.fix && <span className="check-fix"> {channelCheck.fix}</span>}
                </span>
              </div>
            ))}
        </form>
      )}

      <div className="step-actions">
        {!inSettings && (
          <button className="btn btn-ghost" onClick={onContinue}>
            Connect later
          </button>
        )}
        <button className="btn btn-primary" disabled={!connected || !channelCheck?.ok || busy} onClick={() => void save()}>
          {actionLabel(inSettings, final)} {Icon.chevron}
        </button>
      </div>
    </section>
  )
}
