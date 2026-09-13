import { Component, type ReactNode } from 'react'
import type { AppName, Check, RunStatus } from './types'
import { STATUS_LABEL } from './format'

const PINS = [21.5, 29.5, 37.5]

/** The CULPRIT mark: a chip with one faulty pin. Same geometry as brand/logo-mark.svg. */
function LogoMark({ size = 24 }: { size?: number }) {
  return (
    <svg className="logo-mark" viewBox="0 0 64 64" width={size} height={size} aria-hidden>
      <rect x="16" y="16" width="32" height="32" rx="6" fill="none" className="logo-body" strokeWidth="5" />
      {PINS.map((p) => (
        <g key={p} className="logo-pin">
          <rect x={p} y="4" width="5" height="11" rx="1" />
          <rect x={p} y="49" width="5" height="11" rx="1" />
          <rect x="4" y={p} width="11" height="5" rx="1" />
          {p !== 29.5 && <rect x="49" y={p} width="11" height="5" rx="1" />}
        </g>
      ))}
      <rect x="49" y="29.5" width="11" height="5" rx="1" className="logo-culprit" />
    </svg>
  )
}

export function Brand({ size = 24 }: { size?: number }) {
  return (
    <span className="brand">
      <LogoMark size={size} />
      <span className="brand-name">CULPRIT</span>
    </span>
  )
}

export function TrustTag({ trust }: { trust: string }) {
  return <span className={`tag is-${trust}`}>{trust}</span>
}

export function StatusChip({ status }: { status: RunStatus }) {
  return (
    <span className={`chip chip-${status}`}>
      <span className="chip-dot" />
      {STATUS_LABEL[status] ?? status}
    </span>
  )
}

export function Spinner() {
  return <span className="spinner" aria-hidden />
}

const svg = (children: ReactNode, size = 14) => (
  <svg viewBox="0 0 16 16" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden>
    {children}
  </svg>
)

export const Icon = {
  github: svg(
    <>
      <circle cx="4" cy="3.5" r="1.6" />
      <circle cx="4" cy="12.5" r="1.6" />
      <circle cx="12" cy="12.5" r="1.6" />
      <path d="M4 5.1v5.8M12 10.9V7.2a2 2 0 0 0-2-2H7.2M8.6 3.6L7 5.2l1.6 1.6" strokeLinecap="round" strokeLinejoin="round" />
    </>,
  ),
  linear: svg(
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor" stroke="none" />
    </>,
  ),
  slack: svg(<path d="M6.2 2.5L5 13.5M11 2.5L9.8 13.5M2.8 6h11M2.2 10h11" strokeLinecap="round" />),
  check: svg(<path d="M3.2 8.6l3.1 3 6.5-7.2" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />, 12),
  cross: svg(<path d="M4 4l8 8M12 4l-8 8" strokeWidth="2" strokeLinecap="round" />, 11),
  arrow: svg(<path d="M5 11l6-6M6 5h5v5" strokeWidth="1.6" strokeLinecap="round" />, 11),
  gear: svg(
    <>
      <circle cx="8" cy="8" r="2.2" />
      <path d="M8 1.8v1.8M8 12.4v1.8M1.8 8h1.8M12.4 8h1.8M3.6 3.6l1.3 1.3M11.1 11.1l1.3 1.3M3.6 12.4l1.3-1.3M11.1 4.9l1.3-1.3" strokeLinecap="round" />
    </>,
  ),
  chevron: svg(<path d="M6 3.5L10.5 8 6 12.5" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />, 11),
}

export const APP_NAMES: Record<AppName, string> = { github: 'GitHub', linear: 'Linear', slack: 'Slack' }
export const APP_ORDER: AppName[] = ['github', 'linear', 'slack']

/** The result of a live connection check, in plain words. */
export function CheckRow({ check, pending, what = 'Connected as' }: { check: Check | null; pending?: boolean; what?: string }) {
  if (pending) {
    return (
      <div className="check is-pending">
        <Spinner /> Checking with the service…
      </div>
    )
  }
  if (!check) return null
  if (check.connected) {
    return (
      <div className="check is-ok">
        <span className="check-icon">{Icon.check}</span>
        <span>
          {what} <b>{check.identity}</b>
          {check.detail ? <span className="muted"> · {check.detail}</span> : null}
        </span>
        {check.token_hint && <span className="check-hint mono">token {check.token_hint}</span>}
      </div>
    )
  }
  return (
    <div className="check is-bad">
      <span className="check-icon">{Icon.cross}</span>
      <span>
        <b>{check.error}</b>
        {check.fix && <span className="check-fix"> {check.fix}</span>}
      </span>
    </div>
  )
}

interface BoundaryProps {
  resetKey: string
  children: ReactNode
}

/** Keeps one malformed run file from blanking the whole window. */
export class Boundary extends Component<BoundaryProps, { error: string | null }> {
  state = { error: null as string | null }

  static getDerivedStateFromError(err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) }
  }

  componentDidCatch(err: unknown) {
    console.error('CULPRIT: view failed to render', err)
  }

  componentDidUpdate(prev: BoundaryProps) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (this.state.error) return <div className="center-empty">Could not draw this view: {this.state.error}</div>
    return this.props.children
  }
}
