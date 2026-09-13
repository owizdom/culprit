import { useMemo } from 'react'
import type { Blame, Patch, RunMeta } from '../types'
import { tokenDiff, type Tok } from '../format'
import { beforeText, howMade } from '../story'
import { Icon } from '../ui'

const lines = (text: string) =>
  text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)

function Toks({ toks, cls }: { toks: Tok[]; cls: string }) {
  return (
    <>
      {toks.map((t, i) => (
        <span key={i} className={t.changed ? cls : undefined}>
          {t.text}
        </span>
      ))}
    </>
  )
}

export default function FixCard({ meta, patch, blame }: { meta: RunMeta; patch: Patch | null; blame: Blame | null }) {
  const before = useMemo(() => (patch ? lines(beforeText(patch, blame)) : []), [patch, blame])
  const after = useMemo(() => (patch ? lines(patch.replacement) : []), [patch])
  const single = before.length === 1 && after.length === 1 ? tokenDiff(before[0], after[0]) : null

  if (!patch) {
    const text =
      meta.status === 'not_rtl'
        ? `Nothing to fix in the chip. The failure comes from ${meta.verdict?.path ?? 'another file'}.`
        : meta.status === 'abstained'
          ? 'Nothing to fix. The failure did not happen again.'
          : meta.status === 'investigating'
            ? 'Not ready yet.'
            : 'No fix passed the tests.'
    return (
      <section className="fix">
        <div className="sec-head">
          <span className="sec-label">Fix</span>
        </div>
        <p className="sec-empty muted">{text}</p>
      </section>
    )
  }

  const confirmed = patch.trust === 'confirmed'
  const edits = patch.edits && patch.edits.length > 1 ? patch.edits : null
  return (
    <section className="fix">
      <div className="sec-head">
        <span className="sec-label">Fix</span>
        <span className="sec-aside mono">
          {patch.path}:{edits ? edits.map((e) => e.start).join(', ') : patch.start}
          {!edits && patch.end !== patch.start ? `-${patch.end}` : ''}
        </span>
      </div>
      <div className="diff mono">
        {edits &&
          edits.map((e, k) => (
            <div key={`e${k}`}>
              <div className="diff-line muted">
                <span className="diff-sign"> </span>
                <span>line {e.start}</span>
              </div>
              {lines(e.before ?? '').map((l, i) => (
                <div className="diff-line is-del" key={`d${i}`}>
                  <span className="diff-sign">-</span>
                  <span>{l}</span>
                </div>
              ))}
              {lines(e.replacement).map((l, i) => (
                <div className="diff-line is-add" key={`a${i}`}>
                  <span className="diff-sign">+</span>
                  <span>{l}</span>
                </div>
              ))}
            </div>
          ))}
        {!edits && before.map((l, i) => (
          <div className="diff-line is-del" key={`d${i}`}>
            <span className="diff-sign">-</span>
            <span>{single ? <Toks toks={single.before} cls="tok-del" /> : l}</span>
          </div>
        ))}
        {!edits && after.map((l, i) => (
          <div className="diff-line is-add" key={`a${i}`}>
            <span className="diff-sign">+</span>
            <span>{single ? <Toks toks={single.after} cls="tok-add" /> : l}</span>
          </div>
        ))}
      </div>
      <div className={`fix-result ${confirmed ? 'is-ok' : 'is-bad'}`}>
        {confirmed ? Icon.check : Icon.cross}
        <span>{confirmed ? 'All tests pass' : 'Did not pass the tests, proposal only'}</span>
        <span className="muted">· {howMade(patch)}</span>
      </div>
    </section>
  )
}
