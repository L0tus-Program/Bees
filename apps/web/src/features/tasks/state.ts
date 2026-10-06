import type { ActionSummary, TaskRecord } from '../../api/tasks'

export function mergeTaskSnapshot(incoming: TaskRecord[], previous: TaskRecord[]) {
  const known = new Map(previous.map((task) => [task.id, task]))
  return incoming.map((task) => { const prior = known.get(task.id); return prior && prior.revision > task.revision ? prior : task })
}
export function taskOutcomeUnknown(task: TaskRecord) { return task.unknown_requires_ack }
export function taskUnknownKind(task: TaskRecord): 'model' | 'tool' | 'mixed' | null {
  if (!task.unknown_requires_ack) return null
  return task.unknown_model_calls > 0 ? task.unknown_tool_actions > 0 ? 'mixed' : 'model' : 'tool'
}
export function taskAcknowledgmentKey(task: TaskRecord, actions: ActionSummary[] = []) {
  return JSON.stringify([task.revision, task.unknown_model_calls, task.unknown_tool_actions,
    actions.filter((action) => action.status === 'outcome_unknown' && !action.acknowledged).map((action) => action.id).sort()])
}
export function taskControlBlocked(task: TaskRecord, action: string) {
  return task.action_in_flight && ['resume', 'redirect'].includes(action)
}

export function commandIdentity(makeId: () => string = () => crypto.randomUUID()) {
  let last: { signature: string; id: string } | null = null
  return {
    forPayload(payload: unknown) {
      const signature = JSON.stringify(payload)
      if (!last || last.signature !== signature) last = { signature, id: makeId() }
      return last.id
    },
    reset() { last = null },
  }
}
