// Plain words about an investigation, built only from what the engine recorded.
import type { Blame, Patch, RunMeta } from './types'

const CYCLE = /cycle ([\d,]+)/

/** "wrote outside memory at an unknown address (x), cycle 8,222" -> "the CPU wrote outside memory at clock cycle 8,222" */
export function symptom(meta: RunMeta): string {
  const failure = meta.verdict?.failure ?? meta.failure ?? ''
  const cycle = failure.match(CYCLE)?.[1]
  const at = cycle ? ` at cycle ${cycle}` : ''
  const memory = failure.match(/^(wrote|read) outside memory/)
  if (memory) return `the CPU ${memory[1]} outside memory${at}`
  const test = meta.failing_test ?? failure.match(/^(\w+)\.\.ERROR/)?.[1]
  if (test) return `${test} test fails${at}`
  if (failure.startsWith('ERROR!')) return `the regression fails${at}`
  if (failure.startsWith('timeout')) return 'the tests run out of time'
  return failure ? `the tests fail (${failure})` : 'the tests fail'
}

/** "picorv32.v:1246" */
export function location(meta: RunMeta, patch: Patch | null): string {
  const line = patch?.trust === 'confirmed' ? patch.start : meta.verdict?.line
  const path = meta.verdict?.path ?? patch?.path ?? 'picorv32.v'
  return line ? `${path}:${line}` : path
}

export function howMade(patch: Patch): string {
  if (patch.method === 'restore_line') return 'restored one line'
  if (patch.method === 'revert_edit') return 'reverted the edit'
  if (patch.method === 'revert_edits') return `undid the ${patch.edits?.length ?? 2} edits that carry the bug`
  const n = patch.attempts || 1
  return `model patch, ${n} attempt${n === 1 ? '' : 's'}`
}

/** The head text of the lines a patch replaces, from the patch itself or from the pull request's diff. */
export function beforeText(patch: Patch, blame: Blame | null): string {
  if (patch.before) return patch.before
  const h = blame?.hunks.find((x) => x.path === patch.path && x.new_start <= patch.start && patch.start < x.new_start + x.new.length)
  return h ? h.new.slice(patch.start - h.new_start, patch.end - h.new_start + 1).join('') : ''
}

/** The culprit line as it reads in the pull request. */
export function culpritLineText(meta: RunMeta, patch: Patch | null, blame: Blame | null): string {
  if (patch) return beforeText(patch, blame).split('\n').map((l) => l.trim()).find(Boolean) ?? ''
  const line = meta.verdict?.line
  const h = blame?.hunks.find((x) => x.verdict === 'culprit' && line != null && x.new_start <= line && line < x.new_start + x.new.length)
  return h && line != null ? h.new[line - h.new_start].trim() : ''
}
