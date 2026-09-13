// Data contract between the CULPRIT engine (runs/<pr>@<sha12>/, app/connect.py) and the window.

export type Trust = 'confirmed' | 'proposed'
export type RunStatus = 'investigating' | 'confirmed' | 'fixed' | 'resolved' | 'not_rtl' | 'abstained'
export type StepName = 'reproduce' | 'rule_out' | 'confirm' | 'repair' | 'act' | 'resolve' | 'propagation'
export type StepState = 'start' | 'done' | 'fail'

export interface Verdict {
  path: string
  line: number | null
  test?: string | null
  failure?: string | null
  trust: Trust
}

export interface RunMeta {
  id: string
  pr: number
  sha: string
  title: string
  author: string
  base_sha: string
  url?: string
  failing_test: string | null
  failure?: string | null
  trap_cycle: number | null
  status: RunStatus
  started: string
  finished: string | null
  resolved_sha?: string
  sims: number
  seconds: number
  cost_usd: number
  verdict: Verdict | null
}

export interface RunEvent {
  t: string
  step: StepName
  state: StepState
  trust: Trust | null
  text: string
  sims?: number
  data?: Record<string, unknown>
}

export type HunkVerdict = 'inert' | 'culprit' | 'benign' | 'untested'
export type HunkMethod = 'tokens_equal' | 'netlist_equal' | 'revert_pass' | 'revert_fail'

export interface Hunk {
  id: number
  path: string
  new_start: number
  new: string[]
  old: string[]
  verdict: HunkVerdict
  method: HunkMethod | null
}

export interface Fact {
  trust: Trust
  text: string
  path?: string
  line?: number
}

export type BlameKind = 'rtl' | 'non_rtl' | 'interaction' | 'not_reproduced' | 'base_fails' | 'unresolved'

export interface Blame {
  kind: BlameKind
  hunks: Hunk[]
  facts: Fact[]
  sims: number
}

export interface Patch {
  path: string
  start: number
  end: number
  before?: string
  replacement: string
  explanation: string
  trust: Trust
  attempts: number
  result: string
  method?: string
  cost_usd?: number
  edits?: { start: number; end: number; before?: string; replacement: string }[]
}

export type NodeKind = 'line' | 'signal' | 'register' | 'memory' | 'observable'
export type NodeTrust = 'confirmed' | 'proposed' | 'unobservable' | 'contradicted'
export type Sample = [number, string]

export interface PathNode {
  id: string
  label: string
  kind: NodeKind
  src?: string
  trust: NodeTrust
  first_diff_cycle: number | null
  base?: Sample[]
  head?: Sample[]
}

export interface CaptureWindow {
  start: number
  end: number
}

export interface PathData {
  nodes: PathNode[]
  window: CaptureWindow
  observable: { port: string; cycle: number; test?: string; trap_cycle?: number; failure?: string }
}

// apps.json: what the three apps showed when the run last published or resolved.
interface Unreachable {
  skipped?: string
  error?: string
  synced?: string
}

export interface GithubApp extends Unreachable {
  pr_url?: string
  comment_url?: string | null
  suggestion?: boolean
  status?: 'failure' | 'success' | 'pending' | 'error' | null
}

export interface LinearApp extends Unreachable {
  identifier?: string
  state?: string
  assignee?: string | null
  url?: string
}

export interface SlackReply {
  text: string
  ts: string
}

export interface SlackApp extends Unreachable {
  permalink?: string
  replies?: SlackReply[]
}

export interface Apps {
  github?: GithubApp | null
  linear?: LinearApp | null
  slack?: SlackApp | null
}

export interface RunBundle {
  meta: RunMeta
  events: RunEvent[]
  blame: Blame | null
  patch: Patch | null
  apps: Apps | null
}

export interface RepoSummary {
  full_name: string
  private: boolean
  pushed_at: string | null
  description: string | null
}

// Connecting apps (app/connect.py)

export type AppName = 'github' | 'linear' | 'slack'

export interface Check {
  connected: boolean
  identity: string | null
  detail: string | null
  scopes: string[]
  error: string | null
  fix: string | null
  token_hint: string | null
}

export interface Setup {
  repo: string | null
  team: { id: string; key: string | null; name: string | null } | null
  channel: { id: string; name: string | null } | null
}

export interface WatcherState {
  state: 'watching' | 'error' | 'starting' | 'signed_out'
  error: string | null
  checked?: string
}

export interface ToolRow {
  name: string
  ok: boolean
  required: boolean
  version: string | null
  fix: string | null
  note?: string
  image?: boolean
  build?: { state: 'idle' | 'building' | 'done' | 'failed'; log: string }
}

export interface ToolsStatus {
  tools: ToolRow[]
  ready: boolean
}

export interface Connections {
  github: Check
  linear: Check
  slack: Check
  anthropic?: Check
  setup: Setup
  watcher: WatcherState
}

export interface RepoCheck {
  ok: boolean
  name?: string
  private?: boolean
  can_push?: boolean
  workflows?: string[]
  error: string | null
}

export interface LinearTeam {
  id: string
  key: string
  name: string
  done_state_id: string | null
}

export interface ChannelCheck {
  ok: boolean
  id: string | null
  error: string | null
  fix?: string | null
}

export type DeviceStart = { user_code: string; verification_uri: string; interval: number } | { error: string }

export interface DevicePoll {
  state: 'pending' | 'done' | 'expired' | 'denied' | 'error'
  interval?: number
  check?: Check
  error?: string | null
}

export interface TestRunStatus {
  active: boolean
  pr: number | null
  url: string | null
  case: string | null
  title: string | null
  ci: 'none' | 'queued' | 'in_progress' | 'waiting' | 'pending' | 'requested' | 'failure' | 'success' | 'cancelled' | 'skipped'
  run_url: string | null
  investigation: string | null
  investigation_status: RunStatus | null
  posted: boolean
  error: string | null
}

export type PushPayload = { type: 'event'; id: string; event: RunEvent } | { type: 'run_updated'; id: string }
