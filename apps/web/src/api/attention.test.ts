import { afterEach, describe, expect, it, vi } from 'vitest'
import { attentionKinds, getAttention, getAttentionPreferences, setAttentionPreference, setAttentionRead } from './attention'
import type { AttentionItem } from './attention'
import { setCsrfToken } from './client'
import { mergeAttention } from '../features/attention/state'

export const agentId = '10000000-0000-4000-8000-000000000001'
export const attentionFixture: AttentionItem = {
  id: '20000000-0000-4000-8000-000000000001', agent_id: agentId, task_id: '30000000-0000-4000-8000-000000000001', task_title: 'Comparar alternativas', approval_id: null,
  kind: 'task_completed', created_at: '2026-10-12T18:00:00Z', revision: 1, read: false, requires_attention: true, muted: false,
}
const page = (items = [attentionFixture]) => ({ items, next_before: null, unread_count: 1 })
const prefs = attentionKinds.map((kind) => ({ kind, enabled: true, revision: 0 }))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('attention API boundaries', () => {
  it('reads a bounded page with session and no mutation', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(page())); vi.stubGlobal('fetch', fetcher)
    expect((await getAttention(agentId, undefined, 10)).items).toEqual([attentionFixture])
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${agentId}/attention?limit=50&before=10`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { agent_id: '10000000-0000-4000-8000-000000000002' }, { revision: 0 }, { read: 'false' }, { kind: 'tool_secret' },
    { created_at: 'yesterday' }, { created_at: '2026-10-12' }, { approval_id: attentionFixture.id }, { secret: 'private' }, { id: '../../secret' },
  ])('rejects corrupt, foreign and private items: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([{ ...attentionFixture, ...value } as AttentionItem]))))
    await expect(getAttention(agentId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { ...page(), items: [attentionFixture, attentionFixture] }, { ...page(), extra: true }, { ...page(), unread_count: -1 },
    { ...page(), next_before: 0 }, { ...page(), next_before: 20 }, { ...page([]), next_before: 2 },
  ])('rejects ambiguous pages and regressive cursors', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getAttention(agentId, undefined, 10)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('requires complete preferences for the selected bee', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ agent_id: agentId, items: prefs }))
      .mockResolvedValueOnce(Response.json({ agent_id: 'other', items: prefs }))
      .mockResolvedValueOnce(Response.json({ agent_id: agentId, items: [prefs[0], prefs[0], prefs[2], prefs[3]] }))
    vi.stubGlobal('fetch', fetcher)
    expect(await getAttentionPreferences(agentId)).toEqual(prefs)
    await expect(getAttentionPreferences(agentId)).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getAttentionPreferences(agentId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('confirms reading only after canonical CAS and retains the exact identity for a human replay', async () => {
    const command = { client_request_id: crypto.randomUUID(), expected_revision: 1, read: true }
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(Response.json({ ...attentionFixture, read: true, revision: 2 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(setAttentionRead(agentId, attentionFixture, command)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(await setAttentionRead(agentId, attentionFixture, command)).toMatchObject({ read: true, revision: 2 })
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body)
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: 'PUT', headers: { 'X-Bees-CSRF': 'test-csrf' } })
  })
  it.each([
    { revision: 1, read: true }, { revision: 2, read: true, task_id: '30000000-0000-4000-8000-000000000002' },
    { revision: 2, read: true, kind: 'task_failed' }, { revision: 2, read: true, created_at: '2026-10-12T19:00:00Z' },
  ])('rejects false confirmations and substituted bindings', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...attentionFixture, ...value }))); setCsrfToken('test-csrf')
    await expect(setAttentionRead(agentId, attentionFixture, { client_request_id: crypto.randomUUID(), expected_revision: 1, read: true })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('cannot write without CSRF and rejects a preference replay whose state changed again', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ kind: 'task_completed', enabled: true, revision: 3 })); vi.stubGlobal('fetch', fetcher)
    const body = { client_request_id: crypto.randomUUID(), expected_revision: 0, enabled: false }
    await expect(setAttentionPreference(agentId, 'task_completed', body)).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
    setCsrfToken('test-csrf')
    await expect(setAttentionPreference(agentId, 'task_completed', body)).rejects.toMatchObject({ code: 'attention_conflict', status: 409 })
  })
  it('turns an overridden read replay into a conflict instead of another uncertain retry', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...attentionFixture, revision: 3, read: false }))); setCsrfToken('test-csrf')
    await expect(setAttentionRead(agentId, attentionFixture, { client_request_id: crypto.randomUUID(), expected_revision: 1, read: true })).rejects.toMatchObject({ code: 'attention_conflict', status: 409 })
  })
})
describe('attention page state', () => {
  it('deduplicates delivery and refuses to regress a confirmed read', () => {
    const newer = { ...attentionFixture, read: true, revision: 2 }
    expect(mergeAttention([newer], [newer], true)).toEqual([newer])
    expect(() => mergeAttention([newer], [attentionFixture])).toThrow('invalid_response')
    expect(() => mergeAttention([newer], [{ ...newer, read: false }])).toThrow('invalid_response')
  })
  it('updates dynamic resolution without editing the source event or acknowledging an unknown effect', () => {
    const unknown = { ...attentionFixture, kind: 'outcome_unknown' as const, requires_attention: true }
    const read = { ...unknown, read: true, revision: 2 }
    expect(mergeAttention([unknown], [read])[0].requires_attention).toBe(true)
    expect(() => mergeAttention([unknown], [{ ...unknown, task_id: 'other' }])).toThrow('invalid_response')
    expect(mergeAttention([attentionFixture], [{ ...attentionFixture, requires_attention: false, muted: true }])[0]).toMatchObject({ requires_attention: false, muted: true })
  })
})
