import type { PathData, RunBundle } from '../types'
import Header from './Header'
import FixCard from './FixCard'
import BugPath from './BugPath'
import Activity from './Activity'

export default function Investigation({ bundle, path, tracing }: { bundle: RunBundle; path: PathData | null; tracing: boolean }) {
  const { meta, events, patch, blame, apps } = bundle
  return (
    <div className="inv">
      <Header meta={meta} patch={patch} apps={apps} />
      <div className="inv-grid">
        <div className="inv-main">
          <FixCard meta={meta} patch={patch} blame={blame} />
          <BugPath meta={meta} path={path} patch={patch} blame={blame} tracing={tracing} />
        </div>
        <Activity events={events} status={meta.status} />
      </div>
    </div>
  )
}
