import { afterEach, describe, expect, it, vi } from 'vitest'
import { deliveryReference, getDeliveries, getDelivery } from '../../api/deliveryHistory'
import { agentId, artifact, page, result, scope } from './fixtures'
afterEach(() => vi.unstubAllGlobals())
describe('delivery API contracts', () => {
  it('uses authenticated GET, carries filters and cursor, and accepts an empty history', async () => {
    const fetcher = vi.fn(async () => Response.json({ ...page([]), scope: { ...scope, query: 'old' } })); vi.stubGlobal('fetch', fetcher)
    await getDeliveries(agentId, { ...scope, query: ' old ' }, 'opaque-cursor')
    const [url, options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toContain('q=old'); expect(url).toContain('cursor=opaque-cursor'); expect(options.method).toBe('GET'); expect(options.credentials).toBe('same-origin'); expect(options.body).toBeUndefined()
  })
  it.each([
    { ...result, agent_id: '99999999-9999-4999-8999-999999999999' },
    { ...result, task_id: null }, { ...result, run_id: null }, { ...result, version: 0 }, { ...result, status: 'ready' },
  ])('rejects invalid and foreign delivery identity', async (incoming) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(page([incoming]))))
    await expect(getDeliveries(agentId, scope)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects a page with a changed query binding', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...page(), scope: { ...scope, query: 'different' } })))
    await expect(getDeliveries(agentId, scope)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('binds full results to exact message, run and version without replay', async () => {
    const fetcher = vi.fn(async () => Response.json({ item: result, content: 'Old result in full' })); vi.stubGlobal('fetch', fetcher)
    expect(await getDelivery(agentId, result)).toMatchObject({ kind: 'result', content: 'Old result in full' })
    expect((fetcher.mock.calls[0] as unknown as [string])[0]).toContain(`/results/${result.id}?run_id=${result.run_id}`)
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ item: { ...result, version: 2 }, content: 'Wrong version' })))
    await expect(getDelivery(agentId, result)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('accepts a removed artifact as identifiable and rejects replacement identities', async () => {
    const expected = { ...result, kind: 'artifact' as const, id: artifact.id, version: artifact.version, revision: artifact.revision, status: artifact.status }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...artifact, status: 'deleted', revision: artifact.revision + 1 })))
    expect(await getDelivery(agentId, expected)).toMatchObject({ kind: 'artifact', artifact: { status: 'deleted', version: artifact.version } })
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...artifact, version: 2 })))
    await expect(getDelivery(agentId, expected)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('keeps an explicit non-executing reference with exact task, run, conversation and version', () => {
    const reference = deliveryReference(result)
    for (const value of [result.id, result.agent_id, result.task_id, result.run_id, result.conversation_id, 'version=1']) expect(reference).toContain(value)
    expect(reference).toContain('bees:delivery/result/')
  })
})
