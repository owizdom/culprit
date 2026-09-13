import { useEffect, useLayoutEffect, useState, type RefObject } from 'react'
import type { RunStatus, StepName } from './types'

const intFormat = new Intl.NumberFormat('en-US')

const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)

// Formatters tolerate missing engine fields and print a dash instead of throwing.
export const fmtInt = (n: number | null | undefined) => (finite(n) ? intFormat.format(Math.floor(n)) : '–')
export const fmtSeconds = (s: number | null | undefined) => (finite(s) ? `${Math.round(s)} s` : '–')
export const fmtUsd = (n: number | null | undefined) => (finite(n) ? `$${n.toFixed(2)}` : '–')
export const shortSha = (sha: string | null | undefined) => (sha ?? '').slice(0, 7)

/** Compact relative age: 4s, 12m, 3h, 2d. Takes an ISO time or a Slack ts (seconds since the epoch). */
export function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return 'never'
  const t = /^\d+(\.\d+)?$/.test(iso) ? Number(iso) * 1000 : Date.parse(iso)
  if (Number.isNaN(t)) return 'unknown'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h`
  return `${Math.floor(h / 24)}d`
}

export const STATUS_LABEL: Record<RunStatus, string> = {
  investigating: 'Investigating',
  confirmed: 'Culprit found',
  fixed: 'Fixed',
  resolved: 'Resolved',
  not_rtl: 'Not the chip',
  abstained: 'Not reproduced',
}

/** The steps of an investigation, in the order they run, with what the Steps panel says about each. */
export const STEPS: { key: StepName; done: string; active: string }[] = [
  { key: 'reproduce', done: 'Reproduced', active: 'Simulating the pull request and its base' },
  { key: 'rule_out', done: 'Ruled out harmless edits', active: 'Checking which edits cannot change the chip' },
  { key: 'confirm', done: 'Found the culprit', active: 'Undoing edits one at a time' },
  { key: 'repair', done: 'Fixed', active: 'Writing a fix and running every test' },
  { key: 'propagation', done: 'Traced the bug path', active: 'Comparing waveforms' },
  { key: 'act', done: 'Posted', active: 'Posting to GitHub, Linear and Slack' },
  { key: 'resolve', done: 'Resolved', active: 'CI is green, closing the loop' },
]

/** Current time, refreshed on an interval, for live "synced 4s ago" labels. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

/** Rounded border-box size of an element, kept current with a ResizeObserver. */
export function useSize(ref: RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const r = el.getBoundingClientRect()
      const w = Math.round(r.width)
      const h = Math.round(r.height)
      setSize((s) => (s.w === w && s.h === h ? s : { w, h }))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])
  return size
}

export interface Tok {
  text: string
  changed: boolean
}

/** Token-level diff (LCS) of two source lines; changed runs exclude surrounding whitespace. */
export function tokenDiff(before: string, after: string): { before: Tok[]; after: Tok[] } {
  const split = (s: string) => s.match(/\w+|\s+|[^\w\s]/g) ?? []
  const a = split(before)
  const b = split(after)
  const lcs = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  const outA: Tok[] = []
  const outB: Tok[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      outA.push({ text: a[i++], changed: false })
      outB.push({ text: b[j++], changed: false })
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      outA.push({ text: a[i++], changed: true })
    } else {
      outB.push({ text: b[j++], changed: true })
    }
  }
  while (i < a.length) outA.push({ text: a[i++], changed: true })
  while (j < b.length) outB.push({ text: b[j++], changed: true })
  return { before: mergeToks(outA), after: mergeToks(outB) }
}

function mergeToks(toks: Tok[]): Tok[] {
  const out: Tok[] = []
  for (const t of toks) {
    const changed = t.changed
    const last = out[out.length - 1]
    if (last && last.changed === changed) last.text += t.text
    else out.push({ text: t.text, changed })
  }
  // Move whitespace at the edges of a changed run back into the unchanged text.
  const trimmed: Tok[] = []
  for (const t of out) {
    if (!t.changed) {
      trimmed.push({ ...t })
      continue
    }
    const lead = t.text.match(/^\s*/)?.[0] ?? ''
    const tail = t.text.match(/\s*$/)?.[0] ?? ''
    const core = t.text.slice(lead.length, t.text.length - tail.length)
    if (lead) trimmed.push({ text: lead, changed: false })
    trimmed.push({ text: core, changed: true })
    if (tail) trimmed.push({ text: tail, changed: false })
  }
  return trimmed
}
