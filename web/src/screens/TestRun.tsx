import { useEffect, useRef, useState, type ReactNode } from 'react'
import { motion } from 'motion/react'
import { api } from '../bridge'
import type { Connections, TestRunStatus } from '../types'
import { Icon, Spinner } from '../ui'

type StepState = 'pending' | 'active' | 'done' | 'failed'

interface Props {
  conn: Connections | null
  onClose: () => void
  onOpenRun: (id: string) => void
}

const CI_RUNNING = new Set(['queued', 'in_progress', 'waiting', 'pending', 'requested'])

function steps(s: TestRunStatus): { title: string; detail: ReactNode; state: StepState }[] {
  const ciDone = s.ci === 'failure' || s.ci === 'success' || s.ci === 'cancelled' || s.ci === 'skipped'
  const finished = s.investigation_status && s.investigation_status !== 'investigating'
  return [
    {
      title: `Opened pull request #${s.pr}`,
      detail: s.url ? <LinkText href={s.url}>{s.title}</LinkText> : s.title,
      state: 'done',
    },
    {
      title: ciDone ? 'CI finished on GitHub' : 'CI running on GitHub',
      detail: s.run_url ? <LinkText href={s.run_url}>The regression runs the chip's firmware tests in simulation</LinkText> : 'Waiting for GitHub Actions to start the regression',
      state: ciDone ? 'done' : CI_RUNNING.has(s.ci) || s.ci === 'none' ? 'active' : 'pending',
    },
    {
      title: s.ci === 'success' ? 'CI passed, so there was nothing to investigate' : 'CI turned red',
      detail: s.ci === 'failure' ? 'The seeded bug breaks the tests, as it would in a real pull request' : 'Waiting for the result',
      state: s.ci === 'failure' ? 'done' : s.ci === 'success' || s.ci === 'cancelled' ? 'failed' : 'pending',
    },
    {
      title: finished ? 'CULPRIT found the culprit' : 'CULPRIT investigating',
      detail: s.investigation ? 'Reproducing, ruling out harmless edits, proving the culprit, fixing the line' : 'The watcher checks CI every 15 seconds',
      state: finished ? 'done' : s.ci === 'failure' ? 'active' : 'pending',
    },
    {
      title: 'Posted to GitHub, Linear and Slack',
      detail: s.posted ? 'A suggestion on the pull request line, a Linear issue and a Slack thread' : 'After the fix passes the tests',
      state: s.posted ? 'done' : finished ? 'active' : 'pending',
    },
  ]
}

function LinkText({ href, children }: { href: string; children: ReactNode }) {
  return (
    <button className="link" onClick={() => void api.openUrl(href)}>
      {children} {Icon.arrow}
    </button>
  )
}

export default function TestRun({ conn, onClose, onOpenRun }: Props) {
  const [status, setStatus] = useState<TestRunStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const opened = useRef<string | null>(null)

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const s = await api.testRunStatus()
        if (!alive) return
        setStatus(s)
        if (s.investigation && opened.current !== s.investigation) {
          opened.current = s.investigation
          onOpenRun(s.investigation)
        }
      } catch (err) {
        console.error('CULPRIT: test run status failed', err)
      }
    }
    void poll()
    const timer = setInterval(() => void poll(), 3000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [onOpenRun])

  const start = async () => {
    setStarting(true)
    setError(null)
    try {
      const r = await api.startTestRun()
      if ('error' in r) setError(r.error)
      else setStatus(await api.testRunStatus())
    } finally {
      setStarting(false)
    }
  }

  const running = Boolean(status?.active && status.pr)
  const repo = conn?.setup.repo ?? 'the watched repository'

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <motion.section
        className="modal testrun"
        onClick={(e) => e.stopPropagation()}
        initial={{ opacity: 0, y: 12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
      >
        <header className="modal-head">
          <div>
            <div className="eyebrow">Test run</div>
            <h2 className="serif modal-title">Watch CULPRIT handle a real broken pull request</h2>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </header>

        {!running && (
          <>
            <p className="modal-lead">
              CULPRIT opens a real pull request on <span className="mono">{repo}</span> containing a known bug from its test corpus, then handles it exactly as
              it would in production: CI on GitHub turns red, CULPRIT proves the culprit with the simulator, fixes the line, and posts to GitHub, Linear
              and Slack.
            </p>
            <ul className="modal-facts">
              <li>Takes about three minutes, most of it GitHub CI.</li>
              <li>The branch is named culprit-test/…, the only kind of branch Test Run may create. CULPRIT never merges it.</li>
              <li>Each run picks a different bug.</li>
            </ul>
          </>
        )}

        {status?.pr && (
          <ol className="tr-steps">
            {steps(status).map((step) => (
              <li key={step.title} className={`tr-step is-${step.state}`}>
                <span className="check-mark">{step.state === 'done' ? Icon.check : step.state === 'failed' ? Icon.cross : step.state === 'active' ? <Spinner /> : null}</span>
                <span className="tr-body">
                  <span className="tr-title">{step.title}</span>
                  <span className="tr-detail">{step.detail}</span>
                </span>
              </li>
            ))}
          </ol>
        )}

        {(error || status?.error) && <p className="note is-bad">{error ?? status?.error}</p>}

        <footer className="modal-actions">
          {status?.posted && status.url && (
            <button className="btn" onClick={() => void api.openUrl(status.url!)}>
              Open the pull request {Icon.arrow}
            </button>
          )}
          {!running && (
            <button className="btn btn-primary" disabled={starting || !conn?.github.connected} onClick={() => void start()}>
              {starting ? (
                <>
                  <Spinner /> Opening the pull request
                </>
              ) : status?.pr ? (
                'Start another test run'
              ) : (
                'Start test run'
              )}
            </button>
          )}
        </footer>
        {status?.posted && <p className="note">To see the loop close, commit CULPRIT's suggestion on GitHub. When CI turns green, the issue moves to Done.</p>}
      </motion.section>
    </div>
  )
}
