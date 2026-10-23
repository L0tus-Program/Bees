import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { reactivateAgent } from './activation'
import { setCsrfToken } from './client'
const id = '10000000-0000-4000-8000-000000000001'
const body = { expected_revision: 4, expected_safety_revision: 9, consent: 'reactivate_paused_agent' as const }
const receipt = { agent_id: id, revision: 5, status: 'active', safety_revision: 9, global_status: 'stopped', provider_checked: true }
beforeEach(() => setCsrfToken('test-csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
it('sends only the explicit CAS consent with human CSRF and accepts a closed stopped-state receipt', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
  expect(await reactivateAgent(id, body)).toEqual(receipt); expect(fetcher).toHaveBeenCalledOnce()
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${id}/reactivate`); const options = fetcher.mock.calls[0][1]
  expect(options.method).toBe('POST'); expect(JSON.parse(options.body)).toEqual(body); expect(options.headers['X-Bees-CSRF']).toBe('test-csrf'); expect(options.redirect).toBe('error')
})
it('refuses wrong identity, revision, resumed global state, false probe and private extra fields', async () => {
  for (const patch of [{ agent_id: '20000000-0000-4000-8000-000000000001' }, { revision: 4 }, { revision: 6 }, { safety_revision: 10 }, { status: 'paused' }, { global_status: 'running' }, { provider_checked: false }, { private: 'PRIVATE' }]) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...receipt, ...patch })))
    await expect(reactivateAgent(id, body)).rejects.toThrow('invalid_response')
  }
})
it('requires exact consent, UUID, revisions and CSRF before sending', async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  await expect(reactivateAgent('../path', body)).rejects.toThrow('invalid_request')
  await expect(reactivateAgent(id, { ...body, expected_revision: 0 })).rejects.toThrow('invalid_request')
  await expect(reactivateAgent(id, { ...body, expected_safety_revision: 0 })).rejects.toThrow('invalid_request')
  await expect(reactivateAgent(id, { ...body, consent: 'implicit' } as never)).rejects.toThrow('invalid_request')
  setCsrfToken(); await expect(reactivateAgent(id, body)).rejects.toThrow('csrf_missing'); expect(fetcher).not.toHaveBeenCalled()
})
it('does not retry an uncertain mutation or echo private failure text', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('PRIVATE_PROVIDER')); vi.stubGlobal('fetch', fetcher)
  await expect(reactivateAgent(id, body)).rejects.toThrow('mutation_network'); expect(fetcher).toHaveBeenCalledOnce()
})
