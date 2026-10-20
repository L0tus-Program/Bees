import { afterEach, describe, expect, it, vi } from 'vitest'
import { getAttention, getAttentionConversations, setAttentionRead } from './attention'
import { setCsrfToken } from './client'

const agent = '10000000-0000-4000-8000-000000000001', conversation = '40000000-0000-4000-8000-000000000001'
const item = { id: '20000000-0000-4000-8000-000000000001', agent_id: agent, task_id: '30000000-0000-4000-8000-000000000001', task_title: 'Falha própria', approval_id: null, kind: 'task_failed' as const, created_at: '2026-10-20T17:00:00Z', revision: 1, read: false, requires_attention: true, muted: false }
const page = { conversation_id: conversation, items: [item], next_before: null, unread_count: 1 }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('attention conversation boundary', () => {
  it('binds list, cursor and the human read request to the selected conversation', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(page)).mockResolvedValueOnce(Response.json({ ...item, read: true, revision: 2 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf')
    expect((await getAttention(agent, undefined, 10, conversation)).conversation_id).toBe(conversation)
    const command = { client_request_id: crypto.randomUUID(), expected_revision: 1, read: true }
    await setAttentionRead(agent, item, command, undefined, conversation)
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${agent}/attention?limit=50&before=10&conversation_id=${conversation}`)
    expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/agents/${agent}/attention/${item.id}/read?conversation_id=${conversation}`)
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(command)
  })
  it.each([{ ...page, conversation_id: item.task_id }, { items: [item], next_before: null, unread_count: 1 }, { ...page, extra: 'private' }])('refuses a response without the requested scope', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getAttention(agent, undefined, undefined, conversation)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('refuses malformed or nil scope before a request', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    for (const value of ['../foreign', '00000000-0000-0000-0000-000000000000']) await expect(getAttention(agent, undefined, undefined, value)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('reads only bounded conversation metadata and rejects foreign, ambiguous or private catalogs', async () => {
    const catalog = { agent_id: agent, items: [{ id: conversation, title: 'Conversa <script>', created_at: item.created_at }], next_offset: null }
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(catalog)); vi.stubGlobal('fetch', fetcher)
    expect((await getAttentionConversations(agent)).items[0].title).toBe(catalog.items[0].title)
    for (const value of [{ ...catalog, agent_id: conversation }, { ...catalog, items: [catalog.items[0], catalog.items[0]] }, { ...catalog, items: [{ ...catalog.items[0], messages: ['private'] }] }, { ...catalog, next_offset: 50 }]) {
      fetcher.mockResolvedValueOnce(Response.json(value)); await expect(getAttentionConversations(agent)).rejects.toMatchObject({ code: 'invalid_response' })
    }
    await expect(getAttentionConversations(agent, undefined, -1)).rejects.toMatchObject({ code: 'invalid_request' })
  })
})
