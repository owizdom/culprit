// The only door to the engine: the pywebview js_api of the CULPRIT window (app/window.py).
import type {
  AppName,
  ChannelCheck,
  Check,
  Connections,
  DevicePoll,
  DeviceStart,
  LinearTeam,
  PathData,
  PushPayload,
  RepoCheck,
  RepoSummary,
  RunBundle,
  RunMeta,
  Setup,
  TestRunStatus,
  ToolsStatus,
} from './types'

interface SetupChoice {
  repo?: string
  team_id?: string
  channel?: string
  channel_name?: string
}

interface PyApi {
  list_runs(): Promise<RunMeta[]>
  get_run(id: string): Promise<RunBundle | null>
  get_path(id: string): Promise<PathData | null>
  open_url(url: string): Promise<boolean>
  connections(force?: boolean): Promise<Connections>
  github_device_start(): Promise<DeviceStart>
  github_device_poll(): Promise<DevicePoll>
  github_use_cli(): Promise<Check>
  save_token(app: AppName, token: string): Promise<Check>
  disconnect(app: AppName): Promise<Check>
  logout(): Promise<Connections>
  save_anthropic(key: string): Promise<Check>
  tools(): Promise<ToolsStatus>
  build_toolchain(): Promise<{ state: string; log: string }>
  list_repos(): Promise<RepoSummary[] | { error: string }>
  check_repo(fullName: string): Promise<RepoCheck>
  linear_teams(): Promise<LinearTeam[]>
  slack_channel(text: string): Promise<ChannelCheck>
  save_setup(choice: SetupChoice): Promise<{ ok: boolean; setup?: Setup; error?: string }>
  start_test_run(): Promise<{ pr: number; url: string; case: string; title: string } | { error: string }>
  test_run_status(): Promise<TestRunStatus>
}

declare global {
  interface Window {
    pywebview?: { api: PyApi }
    culprit?: { push: (payload: PushPayload) => void }
  }
}

const listeners = new Set<(payload: PushPayload) => void>()
window.culprit = { push: (payload) => listeners.forEach((fn) => fn(payload)) }

/** Receive live pushes from app/window.py (new events, changed run files). */
export function subscribe(fn: (payload: PushPayload) => void): () => void {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

const hasApi = () => typeof window.pywebview?.api?.connections === 'function'

/** The Python API once pywebview has injected it, or null when this page is not inside the CULPRIT window. */
export const ready: Promise<PyApi | null> = new Promise((resolve) => {
  const settle = () => resolve(hasApi() ? window.pywebview!.api : null)
  if (hasApi()) return settle()
  window.addEventListener('pywebviewready', settle, { once: true })
  setTimeout(settle, 6000)
})

async function py(): Promise<PyApi> {
  const api = await ready
  if (!api) throw new Error('CULPRIT runs inside its desktop window')
  return api
}

export const api = {
  listRuns: async () => (await (await py()).list_runs()) ?? [],
  getRun: async (id: string) => (await py()).get_run(id),
  getPath: async (id: string) => (await py()).get_path(id),
  openUrl: async (url: string) => {
    await (await py()).open_url(url)
  },
  connections: async (force = false) => (await py()).connections(force),
  githubDeviceStart: async () => (await py()).github_device_start(),
  githubDevicePoll: async () => (await py()).github_device_poll(),
  githubUseCli: async () => (await py()).github_use_cli(),
  saveToken: async (app: AppName, token: string) => (await py()).save_token(app, token),
  disconnect: async (app: AppName) => (await py()).disconnect(app),
  logout: async () => (await py()).logout(),
  saveAnthropic: async (key: string) => (await py()).save_anthropic(key),
  tools: async () => (await py()).tools(),
  buildToolchain: async () => (await py()).build_toolchain(),
  listRepos: async () => (await py()).list_repos(),
  checkRepo: async (fullName: string) => (await py()).check_repo(fullName),
  linearTeams: async () => (await py()).linear_teams(),
  slackChannel: async (text: string) => (await py()).slack_channel(text),
  saveSetup: async (choice: SetupChoice) => (await py()).save_setup(choice),
  startTestRun: async () => (await py()).start_test_run(),
  testRunStatus: async () => (await py()).test_run_status(),
}

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
