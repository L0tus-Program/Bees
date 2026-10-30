import { ApiError, asApiError } from '../../api/client'
import { checkedObservation, getActivity } from '../../api/activity'
import type { ActivityItem, ActivityPage, ActivityScope } from '../../api/activity'

export interface ActivityView { page: ActivityPage | null; loading: boolean; error: ApiError | null }
export function mergeActivity(previous: ActivityItem[], incoming: ActivityItem[], append: boolean) {
  for (const item of incoming) {
    const old = previous.find((value) => value.id === item.id)
    if (old && (item.seq !== old.seq || item.entity_type !== old.entity_type || item.entity_id !== old.entity_id || item.task_id !== old.task_id || item.run_id !== old.run_id
      || item.action_id !== old.action_id || item.occurred_at !== old.occurred_at || item.revision < old.revision || item.revision === old.revision && item.status !== old.status)) throw new ApiError('invalid_response')
  }
  return append ? [...previous.filter((item) => !incoming.some((value) => item.id === value.id)), ...incoming].sort((a, b) => b.seq - a.seq) : incoming
}
export function groupActivity(items: ActivityItem[]) {
  const groups = new Map<string, { taskId: string | null; title: string | null; runs: Map<string, { runId: string | null; items: ActivityItem[] }> }>()
  for (const item of items) {
    const taskKey = item.task_id ?? 'chat'
    let task = groups.get(taskKey)
    if (!task) { task = { taskId: item.task_id, title: item.task_title, runs: new Map() }; groups.set(taskKey, task) }
    const runKey = item.run_id ?? 'no-run'
    let run = task.runs.get(runKey)
    if (!run) { run = { runId: item.run_id, items: [] }; task.runs.set(runKey, run) }
    run.items.push(item)
  }
  return Array.from(groups.entries(), ([key, group]) => ({ key, ...group, runs: Array.from(group.runs.entries(), ([key, run]) => {
    const actions = new Map<string, { actionId: string | null; items: ActivityItem[] }>()
    for (const item of run.items) {
      const actionKey = item.action_id ?? 'no-action'
      let action = actions.get(actionKey)
      if (!action) { action = { actionId: item.action_id, items: [] }; actions.set(actionKey, action) }
      action.items.push(item)
    }
    return { key, runId: run.runId, actions: Array.from(actions.entries(), ([key, action]) => ({ key, ...action })) }
  }) }))
}

export class ActivityReader {
  state: ActivityView = { page: null, loading: true, error: null }
  private controller: AbortController | null = null
  private stopped = false
  constructor(private agentId: string, private days: number, private scope: ActivityScope, private publish: (value: ActivityView) => void) {}
  private update(value: ActivityView) { this.state = value; this.publish(value) }
  async load(more = false) {
    if (this.stopped || more && (!this.state.page || this.state.page.next_before === null || this.state.error)) return
    this.controller?.abort()
    const operation = new AbortController(); this.controller = operation
    const previous = this.state.page
    this.update({ ...this.state, loading: true, error: null })
    try {
      const page = await getActivity(this.agentId, {
        ...(this.scope.task_id ? { task_id: this.scope.task_id } : {}), ...(this.scope.run_id ? { run_id: this.scope.run_id } : {}),
        ...(more && previous ? { since: previous.interval.since, until: previous.interval.until, before: previous.next_before!, snapshot_seq: previous.snapshot.event_seq } : { window_seconds: this.days * 86400 }),
      }, operation.signal)
      if (operation.signal.aborted || this.stopped) return
      checkedObservation(page.snapshot, previous?.snapshot ?? null)
      const items = mergeActivity(previous?.items ?? [], page.items, more)
      this.update({ page: { ...page, items }, loading: false, error: null })
    } catch (failure) { if (!operation.signal.aborted && !this.stopped) this.update({ ...this.state, loading: false, error: asApiError(failure) }) }
  }
  stop() { this.stopped = true; this.controller?.abort() }
}
