import { useMemo, useState, type ReactNode } from 'react'
import type { Blame, NodeTrust, PathData, PathNode, Patch, RunMeta, Sample } from '../types'
import { fmtInt } from '../format'
import { culpritLineText, location } from '../story'
import { Spinner } from '../ui'
import Waveform from '../Waveform'

interface Props {
  meta: RunMeta
  path: PathData | null
  patch: Patch | null
  blame: Blame | null
  tracing: boolean
}

const TRUST_TAG: Partial<Record<NodeTrust, string>> = { proposed: 'unconfirmed', unobservable: 'not recorded', contradicted: 'never changed' }

function valueAt(samples: Sample[] | undefined, cycle: number): string | null {
  let value: string | null = null
  for (const [t, v] of [...(samples ?? [])].sort((a, b) => a[0] - b[0])) {
    if (t > cycle) break
    value = v
  }
  return value
}

/** Engine values are hex digits; show them short, and x as unknown. */
function shown(value: string | null): string {
  if (value === null) return '?'
  if (/x/i.test(value)) return 'unknown'
  const trimmed = value.replace(/^0+(?=.)/, '')
  return trimmed.length > 1 ? `0x${trimmed}` : trimmed
}

function Section({ aside, children }: { aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="bugpath">
      <div className="sec-head">
        <span className="sec-label">Bug path</span>
        {aside && <span className="sec-aside">{aside}</span>}
      </div>
      {children}
    </section>
  )
}

function Gap({ cycles }: { cycles: number }) {
  return (
    <li className="path-gap">
      <span className="path-gutter" />
      <span className="path-body">
        {fmtInt(cycles)} cycle{cycles === 1 ? '' : 's'} later
      </span>
    </li>
  )
}

export default function BugPath({ meta, path, patch, blame, tracing }: Props) {
  const [open, setOpen] = useState<string | null>(null)
  const parts = useMemo(() => {
    if (!path) return null
    const hops = path.nodes
      .filter((n) => n.kind !== 'line' && n.kind !== 'observable')
      .sort((a, b) => (a.first_diff_cycle ?? Infinity) - (b.first_diff_cycle ?? Infinity))
    return { hops }
  }, [path])

  if (!path || !parts) {
    const text =
      meta.status === 'not_rtl'
        ? `Not the chip: the failure comes from ${meta.verdict?.path ?? 'another file'}.`
        : meta.status === 'abstained'
          ? 'Not reproduced, so there is nothing to trace.'
          : null
    return (
      <Section>
        <p className="sec-empty muted">
          {tracing ? (
            <>
              <Spinner /> Tracing the bug through the chip
            </>
          ) : (
            (text ?? 'No bug path for this run.')
          )}
        </p>
      </Section>
    )
  }

  const { hops } = parts
  const confirmed = hops.filter((h) => h.trust === 'confirmed').length
  const failCycle = path.observable.trap_cycle ?? meta.trap_cycle ?? path.observable.cycle
  const test = path.observable.test ?? meta.failing_test
  const raw = path.observable.failure ?? ''
  const failure = test
    ? `${test} test fails`
    : !raw || raw.startsWith('ERROR!')
      ? 'the regression fails'
      : raw.replace(/,?\s*(at\s+)?cycle [\d,]+$/, '')

  const rows: ReactNode[] = []
  rows.push(
    <li key="line" className="path-row is-line">
      <span className="path-gutter">
        <span className="path-dot" />
      </span>
      <span className="path-body">
        <span className="path-line-head">
          <span className="mono path-name">{location(meta, patch)}</span>
          <span className="path-note">the edited line</span>
        </span>
        <code className="path-code">{culpritLineText(meta, patch, blame)}</code>
      </span>
    </li>,
  )
  let previous: number | null = null
  for (const hop of hops) {
    const cycle = hop.first_diff_cycle
    if (previous !== null && cycle !== null && cycle > previous) rows.push(<Gap key={`gap-${hop.id}`} cycles={cycle - previous} />)
    rows.push(<Hop key={hop.id} hop={hop} path={path} meta={meta} open={open === hop.id} onToggle={() => setOpen((o) => (o === hop.id ? null : hop.id))} />)
    if (cycle !== null) previous = cycle
  }
  if (previous !== null && failCycle > previous) rows.push(<Gap key="gap-fail" cycles={failCycle - previous} />)
  rows.push(
    <li key="fail" className="path-row is-fail">
      <span className="path-gutter">
        <span className="path-dot" />
      </span>
      <span className="path-body">
        <span className="path-fail-text">{failure[0].toUpperCase() + failure.slice(1)}</span>
        <span className="mono path-cycle">cycle {fmtInt(failCycle)}</span>
      </span>
    </li>,
  )

  return (
    <Section aside={`${hops.length} signal${hops.length === 1 ? '' : 's'}, ${confirmed === hops.length ? 'all confirmed' : `${confirmed} confirmed`}`}>
      <ol className="path-list">{rows}</ol>
    </Section>
  )
}

function Hop({ hop, path, meta, open, onToggle }: { hop: PathNode; path: PathData; meta: RunMeta; open: boolean; onToggle: () => void }) {
  const cycle = hop.first_diff_cycle
  const tag = TRUST_TAG[hop.trust]
  const hasWave = (hop.head?.length ?? 0) > 0
  return (
    <li className={`path-row is-hop trust-${hop.trust} ${open ? 'is-open' : ''}`}>
      <span className="path-gutter">
        <span className="path-dot" />
      </span>
      <span className="path-body">
        <button className="path-hop" onClick={onToggle} disabled={!hasWave} title={hasWave ? 'Show the waveform' : undefined}>
          <span className="mono path-name">{hop.label}</span>
          <span className="mono path-cycle">{cycle !== null ? `cycle ${fmtInt(cycle)}` : ''}</span>
          <span className="path-values">
            {cycle !== null ? (
              <>
                should be <b className="mono">{shown(valueAt(hop.base, cycle))}</b>, was <b className="mono is-bad">{shown(valueAt(hop.head, cycle))}</b>
              </>
            ) : null}
            {tag && <span className="path-tag">{tag}</span>}
          </span>
          <span className="mono path-src" title={hop.src}>
            {hop.src ? `:${hop.src.split(':').pop()}` : ''}
          </span>
        </button>
        {open && (
          <div className="path-wave">
            <Waveform node={hop} win={path.window} baseSha={meta.base_sha} headSha={meta.sha} />
          </div>
        )}
      </span>
    </li>
  )
}
