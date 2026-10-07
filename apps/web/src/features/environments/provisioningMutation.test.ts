import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, setCsrfToken } from '../../api/client'
import { agentId, environment, host, plan } from '../../api/provisioning.fixtures'
import { authorizeProvisioning, getProvisioning } from '../../api/provisioning'
import { reconcileProvisioningMutation } from './provisioningMutation'
import { canAuthorizePlan, canPreparePlan } from './provisioningState'

const state = { plan, host, preparation_available: true }
const authorized = { ...plan, status: 'authorized' as const, revision: 2, authorization_expires_at: '2026-10-07T18:15:00Z' }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('provisioning decision reconciliation', () => {
  it('recovers a committed decision after a lost response using one GET and never a repeated POST', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost')).mockResolvedValueOnce(Response.json({ ...state, plan: authorized }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    const notice = vi.fn(); const controller = new AbortController()
    const result = await reconcileProvisioningMutation('authorize',
      () => authorizeProvisioning(agentId, environment.id, plan, 'human-decision-1', controller.signal),
      () => getProvisioning(agentId, environment.id, controller.signal), controller.signal, notice)
    expect(result).toEqual({ state: { ...state, plan: authorized }, notice: 'reconciled' })
    expect(notice).toHaveBeenCalledWith('uncertain')
    expect(fetcher.mock.calls.map((call) => call[1].method)).toEqual(['POST', 'GET'])
    expect(JSON.parse(fetcher.mock.calls[0][1].body).expected_revision).toBe(1)
  })
  it('reconciles a CAS conflict against the new context instead of using the previous confirmation', async () => {
    const stale = { ...plan, context_valid: false, reason_code: 'host_changed' as const }
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ error: { code: 'provisioning_conflict' } }, { status: 409 }))
      .mockResolvedValueOnce(Response.json({ ...state, plan: stale }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    const controller = new AbortController(); const notice = vi.fn()
    const result = await reconcileProvisioningMutation('authorize',
      () => authorizeProvisioning(agentId, environment.id, plan, 'human-decision-1', controller.signal),
      () => getProvisioning(agentId, environment.id, controller.signal), controller.signal, notice)
    expect(result.notice).toBe('conflict'); expect(notice).toHaveBeenCalledWith('conflict')
    expect(canAuthorizePlan(result.state.plan!, result.state, environment)).toBe(false)
    expect(fetcher.mock.calls.map((call) => call[1].method)).toEqual(['POST', 'GET'])
  })
  it.each(['mutation_timeout', 'invalid_response'])('reconciles %s without retrying or offering a replacement for an unknown effect', async (code) => {
    const write = vi.fn().mockRejectedValue(new ApiError(code)); const unknown = { ...plan, status: 'outcome_unknown' as const }
    const read = vi.fn().mockResolvedValue({ ...state, plan: unknown })
    const result = await reconcileProvisioningMutation('authorize', write, read, new AbortController().signal, vi.fn())
    expect(write).toHaveBeenCalledTimes(1); expect(read).toHaveBeenCalledTimes(1)
    expect(canPreparePlan(result.state, environment)).toBe(false); expect(canAuthorizePlan(result.state.plan!, result.state, environment)).toBe(false)
  })
  it('fails closed if reconciliation also fails and does not issue another write', async () => {
    const write = vi.fn().mockRejectedValue(new ApiError('mutation_network')); const read = vi.fn().mockRejectedValue(new ApiError('network_error'))
    const notice = vi.fn()
    await expect(reconcileProvisioningMutation('authorize', write, read, new AbortController().signal, notice)).rejects.toMatchObject({ code: 'network_error' })
    expect(write).toHaveBeenCalledTimes(1); expect(read).toHaveBeenCalledTimes(1); expect(notice).toHaveBeenCalledWith('uncertain')
  })
  it('does not reconcile a definite denied decision or incorrectly report it as saved', async () => {
    const read = vi.fn(); const notice = vi.fn()
    await expect(reconcileProvisioningMutation('authorize', () => Promise.reject(new ApiError('provisioning_denied', 403)), read,
      new AbortController().signal, notice)).rejects.toMatchObject({ status: 403 })
    expect(read).not.toHaveBeenCalled(); expect(notice).not.toHaveBeenCalled()
  })
  it('discards late success when the selected bee or environment has changed and its request was aborted', async () => {
    const controller = new AbortController(); const read = vi.fn()
    await expect(reconcileProvisioningMutation('authorize', async () => { controller.abort(); return authorized }, read,
      controller.signal, vi.fn())).rejects.toMatchObject({ name: 'AbortError' })
    expect(read).not.toHaveBeenCalled()
  })
  it('discards a late reconciled read after the previous view has closed', async () => {
    const controller = new AbortController()
    await expect(reconcileProvisioningMutation('authorize', () => Promise.reject(new ApiError('mutation_network')),
      async () => { controller.abort(); return state }, controller.signal, vi.fn())).rejects.toMatchObject({ name: 'AbortError' })
  })
  it('shows a later revoked replay as revoked rather than as fresh authorization', async () => {
    const revoked = { ...plan, status: 'revoked' as const, revision: 3 }
    const result = await reconcileProvisioningMutation('authorize', () => Promise.resolve(revoked), () => Promise.resolve({ ...state, plan: revoked }),
      new AbortController().signal, vi.fn())
    expect(result.notice).toBe('revoked'); expect(result.state.plan!.usable).toBe(false)
  })
})
