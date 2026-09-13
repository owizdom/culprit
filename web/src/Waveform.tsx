import { useMemo, useRef } from 'react'
import type { CaptureWindow, PathNode, Sample } from './types'
import { fmtInt, shortSha, useSize } from './format'
import { TrustTag } from './ui'

const GUTTER = 64
const RIGHT = 14
const AXIS = 26
const LANE = 26
const CHAR = 6.6 // JetBrains Mono advance at 11px

interface Seg {
  c0: number
  c1: number
  v: string | null
  differs: boolean
}

const sorted = (s?: Sample[]) => [...(s ?? [])].sort((a, b) => a[0] - b[0])

function valueAt(trace: Sample[], c: number): string | null {
  let v: string | null = null
  for (const [t, x] of trace) {
    if (t <= c) v = x
    else break
  }
  return v
}

function segments(trace: Sample[], other: Sample[] | null, v0: number, v1: number): Seg[] {
  const cuts = [v0, ...trace.map((s) => s[0]).filter((c) => c > v0 && c < v1), v1]
  const out: Seg[] = []
  for (let i = 0; i < cuts.length - 1; i++) {
    const c0 = cuts[i]
    const c1 = cuts[i + 1]
    if (c1 <= c0) continue
    const v = valueAt(trace, c0)
    let differs = false
    if (other) {
      const probes = [c0, ...other.map((s) => s[0]).filter((c) => c > c0 && c < c1)]
      differs = probes.some((p) => valueAt(other, p) !== v)
    }
    out.push({ c0, c1, v, differs })
  }
  return out
}

function viewRange(node: PathNode, win: CaptureWindow | null): [number, number] | null {
  const cycles = [...(node.base ?? []), ...(node.head ?? [])].map((s) => s[0])
  if (cycles.length === 0) return null
  const lo = Math.min(...cycles)
  const hi = Math.max(...cycles, node.first_diff_cycle ?? -Infinity)
  const span = Math.max(hi - lo, 6)
  let v0 = lo - Math.max(2, span * 0.15)
  let v1 = hi + Math.max(4, span * 0.35)
  if (win) {
    v0 = Math.max(v0, win.start)
    v1 = Math.min(v1, win.end)
  }
  if (v1 - v0 < 6) v1 = v0 + 6
  return [v0, v1]
}

function ticks(v0: number, v1: number, px: number): number[] {
  const target = Math.max(2, Math.floor(px / 90))
  const raw = (v1 - v0) / target
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)))
  const step = Math.max(1, [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag)
  const out: number[] = []
  for (let c = Math.ceil(v0 / step) * step; c <= v1; c += step) out.push(c)
  return out
}

function fitLabel(v: string | null, px: number): string {
  const text = v ?? 'x'
  if (text.length * CHAR + 10 <= px) return text
  const short = text.replace(/^0+(?=[0-9a-fA-F])/, '')
  return short.length * CHAR + 10 <= px ? short : ''
}

interface Props {
  node: PathNode
  win: CaptureWindow | null
  baseSha: string
  headSha: string
}

export default function Waveform({ node, win, baseSha, headSha }: Props) {
  const plotRef = useRef<HTMLDivElement>(null)
  const { w, h } = useSize(plotRef)

  const model = useMemo(() => {
    const range = viewRange(node, win)
    if (!range) return null
    const [v0, v1] = range
    const base = sorted(node.base)
    const head = sorted(node.head)
    const headSegs = segments(head, base, v0, v1)
    return { v0, v1, base: segments(base, null, v0, v1), head: headSegs, anyDiff: headSegs.some((s) => s.differs) }
  }, [node, win])

  const empty = model
    ? null
    : node.trust === 'unobservable'
      ? `memory not dumped: no waveform for ${node.label}`
      : `no waveform samples for ${node.label}`

  const fd = node.first_diff_cycle ?? null

  return (
    <section className="panel wave">
      <div className="panel-head">
        <span className="kicker">Waveform</span>
        <span className="mono wave-sig">{node.label}</span>
        {node.src && <span className="mono wave-src">{node.src}</span>}
        <TrustTag trust={node.trust} />
        {model && !model.anyDiff && <span className="wave-note">no divergence in the capture window</span>}
        <span className="right wave-legend">
          <span className="legend-line is-base" />
          base <span className="mono">{shortSha(baseSha)}</span>
          <span className="legend-line is-head" />
          head <span className="mono">{shortSha(headSha)}</span>
        </span>
      </div>
      <div className="wave-body">
        <div className="wave-plot" ref={plotRef}>
          {empty ? (
            <div className="wave-empty">{empty}</div>
          ) : (
            model &&
            w > 0 &&
            h > 0 && (
              <Plot
                w={w}
                h={h}
                model={model}
                fd={fd}
                baseSha={baseSha}
                headSha={headSha}
              />
            )
          )}
        </div>
      </div>
    </section>
  )
}

function Plot(p: {
  w: number
  h: number
  model: { v0: number; v1: number; base: Seg[]; head: Seg[] }
  fd: number | null
  baseSha: string
  headSha: string
}) {
  const { w, h, model, fd } = p
  const { v0, v1 } = model
  const left = GUTTER
  const right = w - RIGHT
  const x = (c: number) => left + ((c - v0) / (v1 - v0)) * (right - left)
  const band = (h - AXIS) / 2
  const laneTop = (i: number) => Math.round(AXIS + band * i + (band - LANE) / 2) + 0.5
  const tickList = useMemo(() => ticks(v0, v1, right - left), [v0, v1, right, left])

  const bus = (segs: Seg[], top: number, lane: 'base' | 'head') =>
    segs.map((s, i) => {
      const x0 = x(s.c0)
      const x1 = x(s.c1)
      const slope = Math.min(4, (x1 - x0) / 2)
      const a = x0 <= left + 0.5 ? 0 : slope
      const b = x1 >= right - 0.5 ? 0 : slope
      const mid = top + LANE / 2
      const bot = top + LANE
      const d = `M${x0},${mid} L${x0 + a},${top} L${x1 - b},${top} L${x1},${mid} L${x1 - b},${bot} L${x0 + a},${bot} Z`
      const cls = s.v === null ? 'bus-x' : lane === 'base' ? 'bus-base' : s.differs ? 'bus-diff' : 'bus-same'
      const label = fitLabel(s.v, x1 - x0)
      return (
        <g key={`${lane}-${i}`} className={cls}>
          <path d={d} />
          {label && (
            <text x={(x0 + x1) / 2} y={mid + 3.8} textAnchor="middle">
              {label}
            </text>
          )}
        </g>
      )
    })

  const fdX = fd !== null && fd >= v0 && fd <= v1 ? x(fd) : null
  const flagText = fd !== null ? `diverges at cycle ${fmtInt(fd)}` : ''
  const flagW = flagText.length * 6.2 + 12
  const flagLeft = fdX !== null && fdX + flagW + 4 > right
  // Tick labels give way to the divergence flag and never run past the right edge.
  const flagX0 = fdX === null ? null : flagLeft ? fdX - flagW - 2 : fdX + 2
  const labelFits = (tx: number) =>
    tx + 22 <= w && (flagX0 === null || tx + 24 < flagX0 || tx - 24 > flagX0 + flagW)

  return (
    <svg className="wave-svg" width={w} height={h}>
      <defs>
        <pattern id="wave-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" style={{ stroke: 'var(--border-strong)' }} strokeWidth="1.4" />
        </pattern>
      </defs>

      {tickList.map((c) => {
        const tx = Math.round(x(c)) + 0.5
        return (
          <g key={c} className="wave-tick">
            <line x1={tx} x2={tx} y1={AXIS - 6} y2={h} />
            {labelFits(tx) && (
              <text x={tx} y={12} textAnchor="middle">
                {fmtInt(c)}
              </text>
            )}
          </g>
        )
      })}

      <text className="lane-name" x={0} y={laneTop(0) + 11}>
        base
      </text>
      <text className="lane-sha" x={0} y={laneTop(0) + 24}>
        {shortSha(p.baseSha)}
      </text>
      <text className="lane-name is-head" x={0} y={laneTop(1) + 11}>
        head
      </text>
      <text className="lane-sha" x={0} y={laneTop(1) + 24}>
        {shortSha(p.headSha)}
      </text>

      {bus(model.base, laneTop(0), 'base')}
      {bus(model.head, laneTop(1), 'head')}

      {fdX !== null && (
        <g className="wave-marker">
          <line x1={Math.round(fdX) + 0.5} x2={Math.round(fdX) + 0.5} y1={AXIS - 8} y2={h} />
          <rect x={flagLeft ? fdX - flagW - 2 : fdX + 2} y={1} width={flagW} height={16} rx={3} />
          <text x={flagLeft ? fdX - 8 : fdX + 8} y={12.5} textAnchor={flagLeft ? 'end' : 'start'}>
            {flagText}
          </text>
        </g>
      )}
    </svg>
  )
}
