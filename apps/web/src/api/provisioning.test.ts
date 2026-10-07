import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { authorizeProvisioning, getProvisioning, prepareProvisioning, revokeProvisioning } from './provisioning'
import type { ProvisioningPlan } from './provisioning'
import { agentId, environment, host, plan } from './provisioning.fixtures'
import { authorizationExpired, canAuthorizePlan, canPreparePlan, canRevokePlan, provisioningAttemptIds, reviewKey } from '../features/environments/provisioningState'

const base = `/api/v1/agents/${agentId}/environments/${environment.id}/provisioning`
const authorized: ProvisioningPlan = { ...plan, status: 'authorized', revision: 2, authorization_expires_at: '2026-10-07T18:15:00Z' }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('concrete provisioning plan API boundaries', () => {
  it('reads a review without granting anything or retaining private transport fields', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ plan: { ...plan, credential: 'private', plan: { ...plan.plan, bootstrap: 'private' } }, host: { ...host, token: 'private' }, preparation_available: true }))
    vi.stubGlobal('fetch', fetcher)
    const state = await getProvisioning(agentId, environment.id)
    expect(state).toEqual({ plan, host, preparation_available: true }); expect(JSON.stringify(state)).not.toContain('private')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' }); expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it('prepares only the chosen environment and host revisions with a session CSRF and request UUID', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(plan)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await prepareProvisioning(agentId, environment, host, 'request-1')
    expect(fetcher.mock.calls[0][0]).toBe(`${base}/prepare`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', headers: { 'X-Bees-CSRF': 'csrf-test' } })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ host_id: host.host_id, expected_environment_revision: 1, expected_host_revision: 2, client_request_id: 'request-1' })
  })
  it('authorizes the exact immutable plan hash and human revision; never sends a snapshot, shell or admin permission', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(authorized)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await authorizeProvisioning(agentId, environment.id, plan, 'request-2')
    expect(fetcher.mock.calls[0][0]).toBe(`${base}/${plan.plan_id}/authorize`)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, plan_hash: plan.plan_hash, client_request_id: 'request-2' })
  })
  it('revokes with the same hash and CAS even after the context becomes invalid', async () => {
    const revoked = { ...authorized, status: 'revoked', revision: 3, context_valid: false, reason_code: 'host_changed' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(revoked))); setCsrfToken('csrf-test')
    expect((await revokeProvisioning(agentId, environment.id, authorized, 'request-3')).status).toBe('revoked')
  })
  it('does not send any mutation without a human session CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(prepareProvisioning(agentId, environment, host, 'request-1')).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(authorizeProvisioning(agentId, environment.id, plan, 'request-2')).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(revokeProvisioning(agentId, environment.id, authorized, 'request-3')).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('does not retry an uncertain decision or a conflicting plan', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost')).mockResolvedValueOnce(Response.json({ error: { code: 'provisioning_request_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(authorizeProvisioning(agentId, environment.id, plan, 'request-1')).rejects.toMatchObject({ code: 'mutation_network' })
    await expect(revokeProvisioning(agentId, environment.id, authorized, 'request-2')).rejects.toMatchObject({ status: 409 })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('accepts a replay returning a later state without presenting it as a new authorization', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...authorized, status: 'revoked', revision: 3 }))); setCsrfToken('csrf-test')
    expect((await authorizeProvisioning(agentId, environment.id, plan, 'request-1')).status).toBe('revoked')
  })
  it.each([
    { ...plan, usable: true }, { ...plan, revision: 0 }, { ...plan, status: 'ready' }, { ...plan, plan_hash: 'invalid' },
    { ...plan, context_valid: 'true' }, { ...plan, plan: { ...plan.plan, boot: true } }, { ...plan, plan: { ...plan.plan, network: 'internet' } },
    { ...plan, plan: { ...plan.plan, storage_profile: 'personal-files' } }, { ...plan, plan: { ...plan.plan, vm_name: 'Unrelated machine' } },
    { ...plan, plan: { ...plan.plan, memory_bytes: Number.MAX_SAFE_INTEGER + 1 } }, { ...plan, plan: { ...plan.plan, image_iso_sha256: 'invalid' } },
  ])('rejects malformed or broadened authority in the server plan: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ plan: value, host, preparation_available: true })))
    await expect(getProvisioning(agentId, environment.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { ...plan, plan: { ...plan.plan, agent_id: host.host_id } }, { ...plan, plan: { ...plan.plan, environment_id: host.host_id, vm_name: `Bees-${plan.plan.installation_id}-${host.host_id}` } },
  ])('rejects a plan belonging to another bee or environment: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ plan: value, host, preparation_available: true })))
    await expect(getProvisioning(agentId, environment.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects mutation responses that change resources after the human reviewed the plan', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...authorized, plan: { ...plan.plan, cpu_count: 4 } }))); setCsrfToken('csrf-test')
    await expect(authorizeProvisioning(agentId, environment.id, plan, 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(prepareProvisioning(agentId, environment, host, 'request-2')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
describe('human review context and expiration', () => {
  const state = { plan, host, preparation_available: true }
  it('expires at the deadline and respects the server expiry reason with a slow client clock', () => {
    expect(authorizationExpired(authorized, Date.parse('2026-10-07T18:14:59Z'))).toBe(false)
    expect(authorizationExpired(authorized, Date.parse('2026-10-07T18:15:00Z'))).toBe(true)
    expect(authorizationExpired({ ...authorized, reason_code: 'authorization_expired' }, 0)).toBe(true)
    expect(canAuthorizePlan(authorized, { ...state, plan: authorized }, environment, Date.parse('2026-10-07T18:14:59Z'))).toBe(false)
    expect(canAuthorizePlan(authorized, { ...state, plan: authorized }, environment, Date.parse('2026-10-07T18:15:00Z'))).toBe(true)
  })
  it('requires server context validity plus the exact host and environment snapshot', () => {
    expect(canAuthorizePlan(plan, state, environment)).toBe(true)
    expect(canAuthorizePlan({ ...plan, context_valid: false, reason_code: 'catalog_changed' }, state, environment)).toBe(false)
    expect(canAuthorizePlan(plan, { ...state, host: null }, environment)).toBe(false)
    expect(canAuthorizePlan(plan, { ...state, host: { ...host, revision: 3 } }, environment)).toBe(false)
    expect(canAuthorizePlan(plan, state, { ...environment, revision: 2 })).toBe(false)
    expect(canAuthorizePlan(plan, state, { ...environment, cpu_count: 4 })).toBe(false)
  })
  it('never prepares or authorizes another plan to repeat unknown or in-flight effects', () => {
    for (const status of ['outcome_unknown', 'dispatch_started', 'hardware_verified'] as const) {
      const unknown = { ...plan, status }
      expect(canPreparePlan({ ...state, plan: unknown }, environment)).toBe(false)
      expect(canAuthorizePlan(unknown, { ...state, plan: unknown }, environment)).toBe(false)
    }
    expect(canPreparePlan({ ...state, plan: null, host: null }, environment)).toBe(false)
    expect(canRevokePlan({ ...plan, context_valid: false })).toBe(true)
    const provisioning = { ...environment, status: 'provisioning' as const, reason_code: 'hardware_provisioning' }
    expect(canAuthorizePlan(plan, state, provisioning)).toBe(false)
    expect(canPreparePlan({ ...state, plan: null }, provisioning)).toBe(false)
  })
  it('keeps UUID stable after a lost reply and changes it for a new revision or operation', () => {
    const create = vi.fn().mockReturnValueOnce('request-1').mockReturnValueOnce('request-2'); const identity = provisioningAttemptIds(create)
    expect(identity('authorize:plan:1')).toBe('request-1'); expect(identity('authorize:plan:1')).toBe('request-1')
    expect(identity('authorize:plan:2')).toBe('request-2'); expect(identity('authorize:plan:1')).toBe('request-1')
    expect(create).toHaveBeenCalledTimes(2)
  })
  it('invalidates a human confirmation when context, revision or expiry changes', () => {
    const previous = reviewKey(plan)
    expect(reviewKey({ ...plan })).toBe(previous)
    expect(reviewKey({ ...plan, context_valid: false, reason_code: 'host_changed' })).not.toBe(previous)
    expect(reviewKey({ ...plan, revision: 2 })).not.toBe(previous)
    expect(reviewKey(authorized)).not.toBe(previous)
  })
})
