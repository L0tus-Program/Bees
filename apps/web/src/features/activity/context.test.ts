import { afterEach, describe, expect, it, vi } from 'vitest'
import { rememberActivityContext, rememberWorkspace, restoreActivityContext, restoreWorkspace } from './context'
import { agent, runId, taskId } from './fixtures'
afterEach(() => vi.unstubAllGlobals())
describe('reload context without storing execution data', () => {
  it('stores only per-bee filters and public IDs, preserving an open task across reload', () => {
    const storage = new Map<string, string>()
    vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) })
    const context = { days: 7, scope: { task_id: taskId, run_id: runId }, open_task_id: taskId }
    rememberActivityContext(agent.id, context); expect(restoreActivityContext(agent.id)).toEqual(context)
    expect(restoreActivityContext(taskId)).toEqual({ days: 1, scope: { task_id: null, run_id: null }, open_task_id: null })
    expect(JSON.parse(storage.get(`bees.ui.activity.${agent.id}`)!)).toEqual(context)
    rememberWorkspace(agent.id, 'usage'); expect(restoreWorkspace()).toEqual({ selectedId: agent.id, view: 'usage' })
  })
  it.each([{ days: 99, scope: { task_id: taskId, run_id: runId }, open_task_id: null }, { days: 7, scope: { task_id: null, run_id: runId }, open_task_id: null }, { days: 7, scope: { task_id: '../private', run_id: null }, open_task_id: null }, { days: 7, scope: { task_id: taskId, run_id: runId }, open_task_id: null, secret: 'never' }])('ignores invalid persisted context %j', (value) => {
    vi.stubGlobal('sessionStorage', { getItem: () => JSON.stringify(value) }); expect(restoreActivityContext(agent.id).days).toBe(1)
  })
  it('works when optional browser storage is unavailable', () => {
    vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } })
    expect(restoreActivityContext(agent.id).days).toBe(1); expect(restoreWorkspace().view).toBe('conversation')
    expect(() => rememberActivityContext(agent.id, { days: 1, scope: { task_id: null, run_id: null }, open_task_id: null })).not.toThrow()
  })
})
