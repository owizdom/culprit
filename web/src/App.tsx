import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, ready } from './bridge'
import type { Connections } from './types'
import { Boundary, Brand } from './ui'
import Welcome from './screens/Welcome'
import Connect, { type ConnectStep } from './screens/Connect'
import Ready from './screens/Ready'
import Settings from './screens/Settings'
import Dashboard from './screens/Dashboard'

type Screen =
  | { name: 'boot' }
  | { name: 'outside' }
  | { name: 'welcome' }
  | { name: 'connect'; step: ConnectStep; returnTo: 'ready' | 'settings' }
  | { name: 'ready' }
  | { name: 'settings' }
  | { name: 'dashboard' }

export default function App() {
  const [screen, setScreen] = useState<Screen>({ name: 'boot' })
  const [conn, setConn] = useState<Connections | null>(null)

  const refresh = useCallback(async (force = false) => {
    try {
      const c = await api.connections(force)
      setConn(c)
      return c
    } catch (err) {
      console.error('CULPRIT: could not check connections', err)
      return null
    }
  }, [])

  useEffect(() => {
    void ready.then(async (py) => {
      if (!py) return setScreen({ name: 'outside' })
      const [c, tools] = await Promise.all([refresh(false), api.tools().catch(() => null)])
      if (!(c?.setup.repo && c.github.connected)) return setScreen({ name: 'welcome' })
      // A computer that lost a chip tool (or a fresh install with an old config) fixes that before the dashboard.
      setScreen(tools && !tools.ready ? { name: 'connect', step: 'tools', returnTo: 'settings' } : { name: 'dashboard' })
    })
  }, [refresh])

  // Connection status is checked again whenever the screen changes, and every minute on the dashboard.
  useEffect(() => {
    if (screen.name === 'boot' || screen.name === 'outside') return
    void refresh(false)
    if (screen.name !== 'dashboard') return
    const timer = setInterval(() => void refresh(false), 60000)
    return () => clearInterval(timer)
  }, [screen, refresh])

  let body: ReactNode
  switch (screen.name) {
    case 'boot':
      body = (
        <div className="boot">
          <Brand size={40} />
        </div>
      )
      break
    case 'outside':
      body = (
        <div className="boot">
          <Brand size={40} />
          <p className="muted">CULPRIT runs in its desktop window. Start it with: uv run python culprit.py app</p>
        </div>
      )
      break
    case 'welcome':
      body = <Welcome onStart={() => setScreen({ name: 'connect', step: 'tools', returnTo: 'ready' })} />
      break
    case 'connect':
      body = (
        <Connect
          conn={conn}
          step={screen.step}
          returnTo={screen.returnTo}
          refresh={refresh}
          onStep={(step) => setScreen({ ...screen, step })}
          onFinish={() => setScreen(screen.returnTo === 'ready' ? { name: 'ready' } : { name: 'settings' })}
        />
      )
      break
    case 'ready':
      body = <Ready conn={conn} onOpen={() => setScreen({ name: 'dashboard' })} />
      break
    case 'settings':
      body = (
        <Settings
          conn={conn}
          refresh={refresh}
          onReconnect={(step) => setScreen({ name: 'connect', step, returnTo: 'settings' })}
          onBack={() => setScreen(conn?.setup.repo && conn.github.connected ? { name: 'dashboard' } : { name: 'welcome' })}
          onLoggedOut={() => setScreen({ name: 'welcome' })}
        />
      )
      break
    case 'dashboard':
      body = <Dashboard conn={conn} refresh={refresh} onSettings={() => setScreen({ name: 'settings' })} />
      break
  }

  return (
    <div className="app">
      <Boundary resetKey={screen.name}>{body}</Boundary>
    </div>
  )
}
