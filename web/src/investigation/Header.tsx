import type { ReactNode } from 'react'
import { api } from '../bridge'
import type { Apps, Patch, RunMeta } from '../types'
import { fmtInt, fmtSeconds, fmtUsd, shortSha } from '../format'
import { location, symptom } from '../story'
import { Icon, StatusChip } from '../ui'

interface Link {
  label: string
  icon: ReactNode
  href: string
}

function links(apps: Apps | null): Link[] {
  const gh = apps?.github
  const out: (Link | null)[] = [
    gh && !gh.skipped && (gh.comment_url || gh.pr_url) ? { label: 'GitHub', icon: Icon.github, href: (gh.comment_url || gh.pr_url)! } : null,
    apps?.linear?.url ? { label: apps.linear.identifier ?? 'Linear', icon: Icon.linear, href: apps.linear.url } : null,
    apps?.slack?.permalink ? { label: 'Slack', icon: Icon.slack, href: apps.slack.permalink } : null,
  ]
  return out.filter((l): l is Link => l !== null)
}

function verdict(meta: RunMeta, patch: Patch | null): string[] {
  switch (meta.status) {
    case 'investigating':
      return ['Investigating']
    case 'abstained':
      return ['Not reproduced', 'no line blamed']
    case 'not_rtl':
      return [`Not the chip: ${meta.verdict?.path ?? 'a test or firmware file'}`]
    default: {
      const outcome = meta.status === 'confirmed' ? 'no passing fix yet' : meta.status === 'resolved' ? 'fixed, CI is green' : 'fix passes all tests'
      return [`Culprit ${location(meta, patch)}`, symptom(meta), outcome]
    }
  }
}

export default function Header({ meta, patch, apps }: { meta: RunMeta; patch: Patch | null; apps: Apps | null }) {
  return (
    <header className="inv-head">
      <div className="inv-title-row">
        <span className="mono inv-pr">#{meta.pr}</span>
        <h1 className="inv-title">{meta.title}</h1>
        <StatusChip status={meta.status} />
        <div className="inv-links">
          {links(apps).map((l) => (
            <button key={l.label} className="btn btn-sm" onClick={() => void api.openUrl(l.href)}>
              {l.icon}
              {l.label}
            </button>
          ))}
        </div>
      </div>
      <div className="inv-meta">
        {meta.author} · <span className="mono">{shortSha(meta.sha)}</span> on <span className="mono">{shortSha(meta.base_sha)}</span> · {fmtInt(meta.sims)}{' '}
        simulations · {fmtSeconds(meta.seconds)} · {fmtUsd(meta.cost_usd)}
      </div>
      <div className="inv-verdict">
        {verdict(meta, patch).map((part, i) => (
          <span key={i} className={i === 0 ? 'verdict-main' : undefined}>
            {part}
          </span>
        ))}
      </div>
    </header>
  )
}
