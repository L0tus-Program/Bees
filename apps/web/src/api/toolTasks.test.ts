import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { checkedToolTaskIntent, createToolTask, getToolTaskCapabilities } from './toolTasks'
import { toolTaskCapabilities, toolTaskIntent as intent, toolTaskReceipt } from '../features/tasks/toolTaskFixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('explicit tool task creation', () => {
  it('only probes by authenticated GET and does not create a resource or task', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(toolTaskCapabilities)); vi.stubGlobal('fetch', fetcher)
    expect((await getToolTaskCapabilities(intent.agentId)).available).toBe(true)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
  })
  it.each([{ available: false }, { max_calls: 50 }, { reason: 'enable_everything' }, { resource: '/private' }])('refuses misleading capabilities %#', async (patch) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...toolTaskCapabilities, ...patch })))
    await expect(getToolTaskCapabilities(intent.agentId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('binds creation to the exact request UUID and normalized body, even when replay returns a completed task', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(toolTaskReceipt())); vi.stubGlobal('fetch', fetcher)
    expect((await createToolTask(intent)).status).toBe('completed')
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${intent.agentId}/tool-tasks`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify(intent.body), headers: { 'X-Bees-CSRF': 'csrf' } })
  })
  it.each(['hash', 'uuid', 'task', 'authority'])('does not accept a false HTTP 200 creation receipt: %s', async (kind) => {
    const receipt = toolTaskReceipt()
    if (kind === 'hash') receipt.request_hash = '0'.repeat(64)
    if (kind === 'uuid') receipt.client_request_id = intent.agentId
    if (kind === 'task') receipt.task.objective = 'Other intent'
    if (kind === 'authority') receipt.task.max_calls = 50
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(receipt)))
    await expect(createToolTask(intent)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects arbitrary cached paths, authority, wider limits and file side effects', () => {
    for (const value of [{ ...intent, path: '/tools/execute' }, { ...intent, body: { ...intent.body, resource_id: intent.agentId } }, { ...intent, body: { ...intent.body, max_calls: 50 } }, { ...intent, body: { ...intent.body, deliverables: ['report'] } }]) expect(() => checkedToolTaskIntent(value, intent.agentId)).toThrow('invalid_request')
  })
  it('does not retry an uncertain creation automatically', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('lost')); vi.stubGlobal('fetch', fetcher)
    await expect(createToolTask(intent)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
