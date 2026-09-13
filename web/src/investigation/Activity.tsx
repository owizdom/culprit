import { useState } from 'react'
import type { RunEvent, RunStatus, Trust } from '../types'
import { STEPS } from '../format'
import { Icon, Spinner } from '../ui'

type ItemState = 'pending' | 'active' | 'done' | 'failed'

interface Item {
  key: string
  title: string
  state: ItemState
  trust: Trust | null
  sims: number
  events: RunEvent[]
}

function items(all: RunEvent[], status: RunStatus): Item[] {
  // Re-running the same commit appends to one event log; show only the latest run and what followed it.
  let from = 0
  all.forEach((e, i) => {
    if (e.step === 'reproduce' && e.state === 'start') from = i
  })
  const events = all.slice(from)
  const out: Item[] = STEPS.map((s) => {
    const own = events.filter((e) => e.step === s.key)
    const last = own[own.length - 1]
    const state: ItemState = !last ? 'pending' : last.state === 'start' ? 'active' : last.state === 'done' ? 'done' : 'failed'
    return {
      key: s.key,
      title: state === 'active' ? s.active : s.done,
      state,
      trust: [...own].reverse().find((e) => e.trust)?.trust ?? null,
      sims: own.reduce((sum, e) => sum + (e.sims ?? 0), 0),
      events: own,
    }
  })
  return status === 'investigating' ? out.filter((i) => i.key !== 'resolve') : out.filter((i) => i.state !== 'pending')
}

export default function Activity({ events, status }: { events: RunEvent[]; status: RunStatus }) {
  const [open, setOpen] = useState<string | null>(null)
  const list = items(events, status)
  return (
    <aside className="steps">
      <div className="sec-head">
        <span className="sec-label">Steps</span>
        <span className="sec-aside">
          {list.filter((i) => i.state === 'done').length} of {list.length}
        </span>
      </div>
      <ol className="step-list">
        {list.map((item) => {
          const expanded = open === item.key || item.state === 'failed'
          const details = item.events.filter((e) => e.state !== 'start')
          return (
            <li key={item.key} className={`step-item is-${item.state}`}>
              <button className="step-row" onClick={() => setOpen(open === item.key ? null : item.key)} disabled={details.length === 0}>
                <span className="check-mark">
                  {item.state === 'done' ? Icon.check : item.state === 'failed' ? Icon.cross : item.state === 'active' ? <Spinner /> : null}
                </span>
                <span className="step-name">
                  {item.title}
                  {item.state === 'done' && item.trust === 'proposed' && <span className="path-tag">proposed</span>}
                </span>
                {item.sims > 0 && <span className="step-sims mono">{item.sims}</span>}
              </button>
              {expanded && details.length > 0 && (
                <div className="step-detail">
                  {details.map((e, i) => (
                    <div key={`${e.t}-${i}`}>{e.text}</div>
                  ))}
                </div>
              )}
            </li>
          )
        })}
      </ol>
      <div className="step-foot">Numbers are simulations run. Click a step for its evidence.</div>
    </aside>
  )
}
