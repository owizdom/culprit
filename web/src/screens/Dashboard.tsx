import { useCallback, useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import { api, subscribe } from '../bridge'
import type { Connections, PathData, RunBundle, RunEvent, RunMeta } from '../types'
import { ago, useNow } from '../format'
import { APP_NAMES, APP_ORDER, Boundary, Brand, Icon, StatusChip } from '../ui'
import Investigation from '../investigation/Investigation'
import RepoPicker from './RepoPicker'
import TestRun from './TestRun'

interface View {
  bundle: RunBundle
  path: PathData | null
}

interface Props {
  conn: Connections | null
  refresh: (force?: boolean) => Promise<Connections | null>
  onSettings: () => void
}

const eventKey = (e: RunEvent) => `${e.t}|${e.step}|${e.state}|${e.text}`
const OPEN = new Set(['investigating', 'confirmed', 'fixed'])

export default function Dashboard({ conn, refresh, onSettings }: Props) {
  const [runs, setRuns] = useState<RunMeta[]>([])
  const [loaded, setLoaded] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<View | null>(null)
  const [testRunOpen, setTestRunOpen] = useState(false)
  const [pickerOpen, setPickerOpen] = useState(false)
  const selectedRef = useRef<string | null>(null)

  const refreshList = useCallback(async () => {
    try {
      const list = await api.listRuns()
      setRuns(list)
      setSelectedId((cur) => (cur && list.some((r) => r.id === cur) ? cur : (list[0]?.id ?? null)))
    } catch (err) {
      console.error('CULPRIT: could not list runs', err)
    } finally {
      setLoaded(true)
    }
  }, [])

  const openRun = useCallback(
    (id: string) => {
      setSelectedId(id)
      void refreshList()
    },
    [refreshList],
  )

  const loadRun = useCallback(async (id: string) => {
    try {
      const [bundle, path] = await Promise.all([api.getRun(id), api.getPath(id)])
      if (selectedRef.current !== id) return
      setView(bundle ? { bundle, path } : null)
    } catch (err) {
      console.error(`CULPRIT: could not load run ${id}`, err)
    }
  }, [])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  useEffect(() => {
    selectedRef.current = selectedId
    if (selectedId) void loadRun(selectedId)
    else setView(null)
  }, [selectedId, loadRun])

  useEffect(
    () =>
      subscribe((payload) => {
        if (payload.type === 'run_updated') {
          void refreshList()
          if (payload.id === selectedRef.current) void loadRun(payload.id)
          return
        }
        if (payload.id !== selectedRef.current) {
          void refreshList()
          return
        }
        setView((v) => {
          if (!v || v.bundle.meta.id !== payload.id) return v
          const key = eventKey(payload.event)
          if (v.bundle.events.some((e) => eventKey(e) === key)) return v
          return { ...v, bundle: { ...v.bundle, events: [...v.bundle.events, payload.event] } }
        })
        if (payload.event.step === 'propagation' && payload.event.state === 'done') void loadRun(payload.id)
      }),
    [refreshList, loadRun],
  )

  const events = view?.bundle.events ?? []
  const propagation = events.filter((e) => e.step === 'propagation')
  const tracing = !view?.path && propagation.length > 0 && propagation[propagation.length - 1].state === 'start'
  const watcher = conn?.watcher
  const broken = APP_ORDER.filter((a) => conn && conn[a].token_hint && !conn[a].connected)

  return (
    <div className="dashboard">
      <header className="topbar">
        <Brand size={20} />
        <div className="repo-switch">
          <button
            className={`repo-button ${pickerOpen ? 'is-on' : ''}`}
            onClick={() => setPickerOpen((o) => !o)}
            title={watcher?.state === 'error' ? `Not watching: ${watcher.error}` : 'CULPRIT watches this repository for red CI runs'}
          >
            <span className={`dot ${watcher?.state === 'error' ? 'dot-red' : 'dot-green'}`} />
            <span className="mono">{conn?.setup.repo ?? 'Choose a repository'}</span>
            <span className="chev">{Icon.chevron}</span>
          </button>
          {pickerOpen && (
            <>
              <div className="popover-scrim" onClick={() => setPickerOpen(false)} />
              <div className="popover">
                <div className="popover-title">Repository to watch</div>
                <RepoPicker
                  current={conn?.setup.repo ?? null}
                  saveLabel="Watch this repository"
                  onSaved={async (name) => {
                    const r = await api.saveSetup({ repo: name })
                    if (r.ok) {
                      await refresh(true)
                      setPickerOpen(false)
                      void refreshList()
                    }
                  }}
                />
              </div>
            </>
          )}
        </div>
        <div className="top-spacer" />
        <div className="top-apps">
          {APP_ORDER.map((a) => (
            <span
              key={a}
              className={`app-dot ${conn?.[a].connected ? 'is-on' : ''}`}
              title={conn?.[a].connected ? `${APP_NAMES[a]}: ${conn[a].identity}` : `${APP_NAMES[a]}: not connected`}
            >
              {Icon[a]}
            </span>
          ))}
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => setTestRunOpen(true)}>
          Test run
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onSettings}>
          {Icon.gear} Settings
        </button>
      </header>
      {testRunOpen && <TestRun conn={conn} onClose={() => setTestRunOpen(false)} onOpenRun={openRun} />}

      {(watcher?.state === 'error' || broken.length > 0) && (
        <div className="banner">
          <span>
            {watcher?.state === 'error'
              ? `CULPRIT cannot check CI right now: ${watcher.error}`
              : `${broken.map((a) => APP_NAMES[a]).join(' and ')} stopped working: ${conn?.[broken[0]].error}`}
          </span>
          <button className="btn btn-sm" onClick={onSettings}>
            Fix in Settings
          </button>
        </div>
      )}

      <div className="dash">
        <Inbox runs={runs} selectedId={selectedId} onSelect={setSelectedId} />
        <main className="dash-main">
          {view ? (
            <Boundary resetKey={view.bundle.meta.id}>
              <Investigation key={view.bundle.meta.id} bundle={view.bundle} path={view.path} tracing={tracing} />
            </Boundary>
          ) : (
            <div className="empty-state">
              <Brand size={28} />
              <h2>{loaded ? 'No investigations yet' : 'Loading'}</h2>
              {loaded && (
                <p className="muted">
                  When a pull request on <span className="mono">{conn?.setup.repo}</span> turns CI red, it shows up here. Press Test run to see one now.
                </p>
              )}
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

function Inbox({ runs, selectedId, onSelect }: { runs: RunMeta[]; selectedId: string | null; onSelect: (id: string) => void }) {
  const now = useNow(15000)
  const groups: [string, RunMeta[]][] = [
    ['Open', runs.filter((r) => OPEN.has(r.status))],
    ['Closed', runs.filter((r) => !OPEN.has(r.status))],
  ]
  return (
    <aside className="inbox">
      {runs.length === 0 && <div className="inbox-empty">Broken pull requests show up here.</div>}
      {groups.map(
        ([title, list]) =>
          list.length > 0 && (
            <div className="inbox-group" key={title}>
              <div className="inbox-title">
                {title} <span className="count">{list.length}</span>
              </div>
              <ul>
                <AnimatePresence initial={false}>
                  {list.map((run) => (
                    <motion.li
                      key={run.id}
                      layout
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ type: 'spring', stiffness: 420, damping: 38 }}
                    >
                      <button className={`inbox-row ${run.id === selectedId ? 'is-selected' : ''}`} onClick={() => onSelect(run.id)}>
                        <span className="inbox-row-top">
                          <span className="inbox-title-text">{run.title}</span>
                          <StatusChip status={run.status} />
                        </span>
                        <span className="inbox-meta">
                          <span className="mono">#{run.pr}</span> · {run.author} · {ago(run.started, now)} ago
                        </span>
                      </button>
                    </motion.li>
                  ))}
                </AnimatePresence>
              </ul>
            </div>
          ),
      )}
    </aside>
  )
}
