import type { TaskRecord } from '../../api/tasks'

export function mergeTaskSnapshot(incoming: TaskRecord[], previous: TaskRecord[]) {
  const known = new Map(previous.map((task) => [task.id, task]))
  return incoming.map((task) => { const prior = known.get(task.id); return prior && prior.revision > task.revision ? prior : task })
}
export function taskOutcomeUnknown(task: TaskRecord) { return task.latest_run?.error_code === 'outcome_unknown' }

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
