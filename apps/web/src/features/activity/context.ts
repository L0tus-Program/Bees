import { exact, uuid } from '../../api/activity'
import type { ActivityScope } from '../../api/activity'

export interface ActivityContext { days: number; scope: ActivityScope; open_task_id: string | null }
const empty = (): ActivityContext => ({ days: 1, scope: { task_id: null, run_id: null }, open_task_id: null })
export function restoreActivityContext(agentId: string): ActivityContext {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(`bees.ui.activity.${agentId}`) ?? 'null')
    if (exact(value, ['days', 'scope', 'open_task_id']) && [1, 7, 30].includes(value.days as number)
      && exact(value.scope, ['task_id', 'run_id']) && (value.scope.task_id === null || uuid(value.scope.task_id))
      && (value.scope.run_id === null || uuid(value.scope.run_id) && value.scope.task_id !== null)
      && (value.open_task_id === null || uuid(value.open_task_id))) return value as unknown as ActivityContext
  } catch { /* Session storage is optional; stored data never grants authority. */ }
  return empty()
}
export function rememberActivityContext(agentId: string, value: ActivityContext) {
  try { sessionStorage.setItem(`bees.ui.activity.${agentId}`, JSON.stringify(value)) } catch { /* Optional. */ }
}
export const workspaceViews = ['conversation', 'profile', 'model', 'memories', 'deliveries', 'skills', 'routines', 'policies', 'usage', 'attention', 'tools', 'computer'] as const
export type WorkspaceView = typeof workspaceViews[number]
export function restoreWorkspace(): { selectedId: string | null; view: WorkspaceView } {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem('bees.ui.workspace') ?? 'null')
    if (exact(value, ['selectedId', 'view']) && (value.selectedId === null || uuid(value.selectedId)) && workspaceViews.includes(value.view as WorkspaceView)) return value as { selectedId: string | null; view: WorkspaceView }
  } catch { /* Optional. */ }
  return { selectedId: null, view: 'conversation' }
}
export function rememberWorkspace(selectedId: string | null, view: WorkspaceView) {
  try { sessionStorage.setItem('bees.ui.workspace', JSON.stringify({ selectedId, view })) } catch { /* Optional. */ }
}
