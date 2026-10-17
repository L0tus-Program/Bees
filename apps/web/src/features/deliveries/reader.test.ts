import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { getDeliveries } from '../../api/deliveryHistory'
import { DeliveryReader } from './reader'
import { agentId, page, result, scope } from './fixtures'
vi.mock('../../api/deliveryHistory', async (original) => ({ ...await original<typeof import('../../api/deliveryHistory')>(), getDeliveries: vi.fn() }))
afterEach(() => vi.resetAllMocks())
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
describe('delivery reader generations and pagination', () => {
  it('appends real cursor pages and rejects duplicates rather than showing misleading history', async () => {
    const incoming = getDeliveries as ReturnType<typeof vi.fn>
    incoming.mockResolvedValueOnce(page([result], 'cursor1')).mockResolvedValueOnce(page([{ ...result, id: '99999999-9999-4999-8999-999999999999' }]))
    const reader = new DeliveryReader(agentId, scope, vi.fn())
    await reader.load(); await reader.load(true)
    expect(reader.state.page?.items).toHaveLength(2); expect(incoming.mock.calls[1][2]).toBe('cursor1')
    incoming.mockResolvedValueOnce(page([result], 'cursor1')).mockResolvedValueOnce(page([result]))
    await reader.load(); await reader.load(true)
    expect(reader.state.error?.code).toBe('invalid_response'); expect(reader.state.page?.items).toHaveLength(1)
  })
  it('rejects changing snapshot boundaries between pages', async () => {
    vi.mocked(getDeliveries).mockResolvedValueOnce(page([result], 'cursor1')).mockResolvedValueOnce({ ...page([]), until: '2026-10-17T12:00:00+00:00' })
    const reader = new DeliveryReader(agentId, scope, vi.fn()); await reader.load(); await reader.load(true)
    expect(reader.state.error?.code).toBe('invalid_response')
  })
  it('aborts and fences stale responses after cancellation even when transport ignores the signal', async () => {
    const pending = deferred<ReturnType<typeof page>>(); const publish = vi.fn()
    vi.mocked(getDeliveries).mockReturnValue(pending.promise)
    const reader = new DeliveryReader(agentId, scope, publish); const operation = reader.load()
    reader.cancel(); pending.resolve(page()); await operation
    expect(reader.state.cancelled).toBe(true); expect(reader.state.page).toBeNull(); expect(reader.state.loading).toBe(false)
    expect(vi.mocked(getDeliveries).mock.calls[0][3]?.aborted).toBe(true)
  })
  it('fences responses from old query and bee readers, including after stop', async () => {
    const old = deferred<ReturnType<typeof page>>(); const publish = vi.fn()
    vi.mocked(getDeliveries).mockReturnValueOnce(old.promise).mockResolvedValueOnce(page([]))
    const first = new DeliveryReader(agentId, scope, publish); const operation = first.load(); first.stop()
    const second = new DeliveryReader(agentId, { ...scope, query: 'new query' }, publish); await second.load()
    old.resolve(page()); await operation
    expect(publish.mock.calls.at(-1)?.[0].page.items).toEqual([]); expect(second.state.page?.items).toEqual([])
  })
  it('shows failures explicitly and retains only earlier validated pages', async () => {
    vi.mocked(getDeliveries).mockResolvedValueOnce(page([result], 'cursor')).mockRejectedValueOnce(new ApiError('network_error'))
    const reader = new DeliveryReader(agentId, scope, vi.fn()); await reader.load(); await reader.load(true)
    expect(reader.state.error?.code).toBe('network_error'); expect(reader.state.page?.items).toEqual([result])
    const requests = vi.mocked(getDeliveries).mock.calls.length; await reader.load(true)
    expect(vi.mocked(getDeliveries)).toHaveBeenCalledTimes(requests)
  })
})
