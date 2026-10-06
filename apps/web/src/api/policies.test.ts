import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { createPolicy, getPolicies, updatePolicy } from './policies'
import type { PolicyRecord } from './policies'
import { editableGenerationPolicy, generationScope, mergePolicyPage, policyRequestIdentity, revocablePolicy } from '../features/policies/state'
import type { ModelConfig } from './onboarding'

const config: ModelConfig = { kind: 'openai_compatible', endpoint: 'https://example.test/v1', model: 'model-1', capabilities: { text: true, tool_calls: false } }
const stamp = '2026-10-05T18:00:00Z'
const policy: PolicyRecord = { id: 'rule-1', agent_id: 'bee-1', name: 'Modelo atual', effect: 'deny', scope: generationScope(config, true), status: 'active', revision: 1, reason: '', created_at: stamp, updated_at: stamp }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('policy API contracts', () => {
  it('lists personal and global rules with authenticated read-only GET', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ policies: [policy, { ...policy, id: 'global', agent_id: null }], has_more: false, next_offset: null }))
    vi.stubGlobal('fetch', fetcher)
    expect((await getPolicies('bee-1')).policies).toHaveLength(2)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { ...policy, agent_id: 'other-bee' }, { ...policy, revision: 0 }, { ...policy, effect: 'approve' },
    { ...policy, scope: [] }, { ...policy, scope: null }, { ...policy, updated_at: 'yesterday' },
  ])('rejects unsafe or mismatched rules: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ policies: [value], has_more: false })))
    await expect(getPolicies('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects duplicate IDs and a false mutation result for another agent', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ policies: [policy, policy], has_more: false })).mockResolvedValueOnce(Response.json({ ...policy, agent_id: null }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(getPolicies('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(createPolicy('bee-1', { name: policy.name, effect: policy.effect, scope: policy.scope, reason: '', client_request_id: 'uuid-1' })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('updates with PATCH, session CSRF and optimistic revision including revocation', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...policy, revision: 2, status: 'revoked' }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    const body = { expected_revision: 1, name: policy.name, effect: policy.effect, scope: policy.scope, reason: '', status: 'revoked' as const }
    await updatePolicy('bee-1', 'rule-1', body)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/policies/rule-1')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'PATCH', headers: { 'X-Bees-CSRF': 'csrf-test' }, body: JSON.stringify(body) })
  })
  it('cannot mutate without CSRF and never automatically retries a conflict', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'state_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher)
    const body = { name: policy.name, effect: policy.effect, scope: policy.scope, reason: '', client_request_id: 'uuid-1' }
    await expect(createPolicy('bee-1', body)).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
    setCsrfToken('csrf-test')
    await expect(createPolicy('bee-1', body)).rejects.toMatchObject({ code: 'state_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('loads additional pages without granting or changing any rule', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ policies: [policy], has_more: true, next_offset: 101 }))
    vi.stubGlobal('fetch', fetcher)
    const result = await getPolicies('bee-1', undefined, 100)
    expect(result.next_offset).toBe(101)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/policies?offset=100&limit=100')
    expect(fetcher.mock.calls[0][1].method).toBe('GET')
  })
  it.each([
    { policies: [policy], has_more: true, next_offset: null },
    { policies: [policy], has_more: true, next_offset: 0 },
    { policies: [], has_more: true, next_offset: 100 },
    { policies: [policy], has_more: false, next_offset: 1 },
  ])('rejects pagination that could loop, skip or misstate remaining records: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getPolicies('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('revokes an advanced owned scope without copying or revalidating its scope', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...policy, status: 'revoked', revision: 2, scope: { tool_name: 'shell', parameters: { unknown: 'value' } } }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await updatePolicy('bee-1', policy.id, { expected_revision: 1, status: 'revoked' })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, status: 'revoked' })
  })
})
describe('policy editor boundaries', () => {
  it('current scope binds exactly to saved endpoint and model without leaking credentials', () => {
    expect(generationScope({ ...config, secret_ref: 'vault:test' }, true)).toEqual({ tool_name: 'model', action: 'generate', environment_id: 'control_plane', resource: config.endpoint, parameters: { model: config.model } })
    expect(generationScope(null, false)).toEqual({ tool_name: 'model', action: 'generate', environment_id: 'control_plane' })
    expect(() => generationScope(null, true)).toThrow('model_not_configured')
  })
  it('prevents editing global, unrelated tool or advanced identity scopes', () => {
    expect(editableGenerationPolicy(policy, 'bee-1')).toBe(true)
    expect(editableGenerationPolicy({ ...policy, agent_id: null }, 'bee-1')).toBe(false)
    expect(editableGenerationPolicy({ ...policy, scope: { ...policy.scope, tool_name: 'shell' } }, 'bee-1')).toBe(false)
    expect(editableGenerationPolicy({ ...policy, scope: { ...policy.scope, identity: 'another-account' } }, 'bee-1')).toBe(false)
    expect(editableGenerationPolicy({ ...policy, scope: { ...policy.scope, parameters: { model: 'x', cost: 100 } } }, 'bee-1')).toBe(false)
  })
  it('explicit create retries reuse the UUID only for identical input', () => {
    let count = 0; const identity = policyRequestIdentity(() => `uuid-${++count}`)
    expect(identity({ name: 'a' })).toBe('uuid-1'); expect(identity({ name: 'a' })).toBe('uuid-1')
    expect(identity({ name: 'b' })).toBe('uuid-2')
  })
  it('allows revoking an active advanced owned rule but never a global, foreign or revoked one', () => {
    expect(revocablePolicy({ ...policy, scope: { tool_name: 'shell' } }, 'bee-1')).toBe(true)
    expect(revocablePolicy({ ...policy, agent_id: null }, 'bee-1')).toBe(false)
    expect(revocablePolicy(policy, 'other-bee')).toBe(false)
    expect(revocablePolicy({ ...policy, status: 'revoked' }, 'bee-1')).toBe(false)
  })
  it('loads legacy malformed scopes for safe status-only revocation while refusing to edit them', async () => {
    const malformed = { ...policy, scope: { tool_name: 123, parameters: ['untrusted'], identity: { invalid: true } } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ policies: [malformed], has_more: false, next_offset: null })))
    const loaded = (await getPolicies('bee-1')).policies[0]
    expect(editableGenerationPolicy(loaded, 'bee-1')).toBe(false)
    expect(revocablePolicy(loaded, 'bee-1')).toBe(true)
    expect(loaded.scope).toEqual(malformed.scope)
  })
  it('deduplicates shifting pages and keeps a newer acknowledged revision', () => {
    const previous = { policies: [{ ...policy, revision: 3 }], has_more: true, next_offset: 100 }
    const incoming = { policies: [policy, { ...policy, id: 'rule-101' }], has_more: false, next_offset: null }
    expect(mergePolicyPage(previous, incoming)).toEqual({ policies: [{ ...policy, revision: 3 }, incoming.policies[1]], has_more: false, next_offset: null })
  })
})
