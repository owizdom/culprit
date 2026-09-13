import { useEffect, useMemo, useState } from 'react'
import { api } from '../bridge'
import type { RepoCheck, RepoSummary } from '../types'
import { ago, useNow } from '../format'
import { Icon, Spinner } from '../ui'

interface Props {
  current: string | null
  saveLabel: string
  onSaved: (repo: string) => Promise<void>
}

const FULL_NAME = /^[\w.-]+\/[\w.-]+$/

/** Search the repositories you can see, check one has CI, and pick it. */
export default function RepoPicker({ current, saveLabel, onSaved }: Props) {
  const now = useNow(60000)
  const [repos, setRepos] = useState<RepoSummary[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const [check, setCheck] = useState<RepoCheck | null>(null)
  const [busy, setBusy] = useState<'check' | 'save' | null>(null)

  useEffect(() => {
    void api.listRepos().then(
      (r) => (Array.isArray(r) ? setRepos(r) : setLoadError(r.error)),
      (err) => setLoadError(String(err)),
    )
  }, [])

  const q = query.trim().toLowerCase()
  const shown = useMemo(() => (repos ?? []).filter((r) => !q || r.full_name.toLowerCase().includes(q)).slice(0, 60), [repos, q])
  const typed = FULL_NAME.test(query.trim()) && !shown.some((r) => r.full_name.toLowerCase() === q) ? query.trim() : null

  const choose = async (name: string) => {
    setChosen(name)
    setCheck(null)
    setBusy('check')
    try {
      setCheck(await api.checkRepo(name))
    } finally {
      setBusy(null)
    }
  }

  const save = async () => {
    if (!check?.ok || !check.name) return
    setBusy('save')
    try {
      await onSaved(check.name)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="repo-picker">
      <input className="input" autoFocus placeholder="Search your repositories, or type owner/name" value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="repo-list">
        {repos === null && !loadError && (
          <div className="repo-empty">
            <Spinner /> Loading your repositories
          </div>
        )}
        {loadError && <div className="repo-empty is-bad">{loadError}</div>}
        {typed && (
          <button className={`repo-row ${chosen === typed ? 'is-on' : ''}`} onClick={() => void choose(typed)}>
            <span className="repo-name mono">{typed}</span>
            <span className="repo-time">use this repository</span>
          </button>
        )}
        {shown.map((r) => (
          <button key={r.full_name} className={`repo-row ${chosen === r.full_name ? 'is-on' : ''}`} onClick={() => void choose(r.full_name)} title={r.description ?? undefined}>
            <span className="repo-name mono">
              {r.full_name}
              {r.full_name === current && <span className="repo-current"> watching</span>}
            </span>
            {r.private ? <span className="repo-badge">private</span> : <span />}
            <span className="repo-time">{r.pushed_at ? `pushed ${ago(r.pushed_at, now)} ago` : ''}</span>
          </button>
        ))}
        {repos && shown.length === 0 && !typed && <div className="repo-empty">Nothing matches. Type owner/name to use another repository.</div>}
      </div>
      {busy === 'check' && (
        <div className="check is-pending">
          <Spinner /> Checking {chosen} for CI runs
        </div>
      )}
      {check &&
        (check.ok ? (
          <div className="check is-ok">
            <span className="check-icon">{Icon.check}</span>
            <span>
              <b>{check.name}</b>
              <span className="muted">{check.workflows?.length ? ` · CI: ${check.workflows.join(', ')}` : ' · no CI workflow found'}</span>
            </span>
          </div>
        ) : (
          <div className="check is-bad">
            <span className="check-icon">{Icon.cross}</span>
            <b>{check.error}</b>
          </div>
        ))}
      <div className="repo-actions">
        <button className="btn btn-primary" disabled={!check?.ok || busy !== null} onClick={() => void save()}>
          {busy === 'save' ? (
            <>
              <Spinner /> Saving
            </>
          ) : (
            saveLabel
          )}
        </button>
      </div>
    </div>
  )
}
