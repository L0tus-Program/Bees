import { afterEach, describe, expect, it, vi } from 'vitest'
import { getDelegations } from './delegations'
import { messages, sendChat } from './onboarding'
import { setCsrfToken } from './client'
import { delegation, stamp } from '../features/chat/testFixtures'
import { commandIdentity } from '../features/tasks/state'
import { composerReducer, emptyComposer } from '../features/chat/composer'

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('chat delegation contracts', () => {
  it('recovers linked tasks through a read-only scoped endpoint with their full answer', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ delegations: [delegation], has_more: false, next_offset: null }))
    vi.stubGlobal('fetch', fetcher)
    const result = await getDelegations('bee-1', 'chat-1')
    expect(result.delegations[0].task.latest_run?.result?.content).toBe(delegation.task.latest_run?.result?.content)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/delegations?conversation_id=chat-1&offset=0&limit=100')
    expect(fetcher.mock.calls[0][1].method).toBe('GET')
  })

  it.each([
    { ...delegation, task: { ...delegation.task, agent_id: 'other-bee' } },
    { ...delegation, id: 'other-task' }, { ...delegation, source_message_id: null },
    { ...delegation, response_message_id: '' }, { ...delegation, task: { ...delegation.task, status: 'invented' } },
  ])('rejects invalid projections instead of attaching unrelated content: %j', async (item) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ delegations: [item], has_more: false, next_offset: null })))
    await expect(getDelegations('bee-1', 'chat-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects duplicate tasks and inconsistent pagination', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ delegations: [delegation, delegation], has_more: false, next_offset: null }))
      .mockResolvedValueOnce(Response.json({ delegations: [], has_more: true, next_offset: null }))
    vi.stubGlobal('fetch', fetcher)
    await expect(getDelegations('bee-1', 'chat-1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getDelegations('bee-1', 'chat-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('sends explicit per-message consent, defaults to false and keeps CSRF', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json({})))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf')
    await sendChat('bee-1', 'chat-1', 'Converse comigo')
    await sendChat('bee-1', 'chat-1', 'Faça uma redação', undefined, true, 'request-1')
    expect(JSON.parse(fetcher.mock.calls[0][1].body).allow_delegation).toBe(false)
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ conversation_id: 'chat-1', content: 'Faça uma redação', allow_delegation: true, client_request_id: 'request-1' })
    expect(fetcher.mock.calls[1][1].headers['X-Bees-CSRF']).toBe('csrf')
  })

  it('never retries a failed chat, preserves identity for human reconciliation and removes consent for an edited request', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('lost response'))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf')
    let state = composerReducer(emptyComposer, { type: 'draft', value: 'Crie uma redação' })
    state = composerReducer(state, { type: 'consent', value: true })
    const identity = commandIdentity(() => crypto.randomUUID())
    const payload = () => ({ content: state.draft, allow_delegation: state.allowDelegation })
    const first = identity.forPayload(payload())
    await expect(sendChat('bee-1', 'chat-1', state.draft, undefined, state.allowDelegation, first)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    state = composerReducer(state, { type: 'failure' })
    expect(state.allowDelegation).toBe(true)
    expect(identity.forPayload(payload())).toBe(first)
    state = composerReducer(state, { type: 'draft', value: 'Crie outra redação' })
    expect(state.allowDelegation).toBe(false)
    expect(identity.forPayload(payload())).not.toBe(first)
    expect(composerReducer(state, { type: 'success' })).toEqual(emptyComposer)
  })

  it('recovers a nullable assistant tool turn and durable refusal without inventing text', async () => {
    const message = { id: 'message-assistant', role: 'assistant', content: null, created_at: stamp, delegation_results: [{ tool_call_id: 'call-1', status: 'rejected', code: 'pending_task_limit', task_id: null }] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ conversation_id: 'chat-1', messages: [message], has_more: false })))
    expect((await messages('bee-1', 'chat-1')).messages).toEqual([message])
  })

  it('refuses malformed tool outcome metadata', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ conversation_id: 'chat-1', messages: [{ id: 'message', role: 'assistant', content: null, created_at: stamp, delegation_results: [{ tool_call_id: 'call-1', status: 'created', code: 'fake', task_id: null }] }] })))
    await expect(messages('bee-1', 'chat-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
