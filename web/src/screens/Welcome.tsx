import { APP_NAMES, APP_ORDER, Brand, Icon } from '../ui'

export default function Welcome({ onStart }: { onStart: () => void }) {
  return (
    <div className="welcome">
      <section className="welcome-col">
        <Brand size={32} />
        <h1 className="welcome-title serif">A debugging agent for chip teams.</h1>
        <p className="welcome-lead">
          One agent across GitHub, Linear and Slack. When a pull request turns CI red, CULPRIT proves which line did it with the simulator, fixes
          it, and tells your team.
        </p>
        <div className="welcome-apps">
          {APP_ORDER.map((app) => (
            <span className="welcome-app" key={app}>
              {Icon[app]} {APP_NAMES[app]}
            </span>
          ))}
        </div>
        <button className="btn btn-primary btn-lg" onClick={onStart}>
          Get started {Icon.chevron}
        </button>
        <p className="welcome-foot">Tokens stay in .env on this computer.</p>
      </section>
    </div>
  )
}
