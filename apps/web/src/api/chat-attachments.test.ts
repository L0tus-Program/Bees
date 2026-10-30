import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { messages, sendChat } from './onboarding'
import { agentId, artifact, conversationId } from '../features/artifacts/fixtures'
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
const ref = { artifact_id: artifact.id, revision: artifact.revision, sha256: artifact.sha256! }
const metadata = { ...ref, name: artifact.name, media_type: artifact.media_type, size_bytes: artifact.size_bytes, source: artifact.source }
const turn = { attachments: [ref], attachment_consent: { expected_agent_revision: 7 } }
describe('explicit attachment use on one chat turn', () => {
  it('sends only immutable references and explicit destination revision; no raw content or paths', async () => {
    setCsrfToken('test-csrf'); const fetcher = vi.fn().mockResolvedValue(Response.json({ ok: true })); vi.stubGlobal('fetch', fetcher)
    await sendChat(agentId, conversationId, 'Use este material', undefined, false, artifact.id, turn)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ conversation_id: conversationId, content: 'Use este material', allow_delegation: false, client_request_id: artifact.id, ...turn })
  })
  it.each([{ attachments: [], attachment_consent: { expected_agent_revision: 7 } }, { ...turn, attachments: [ref, ref] }, { ...turn, attachment_consent: { expected_agent_revision: 0 } }, { ...turn, attachments: [{ ...ref, sha256: 'wrong' }] }])('rejects malformed consent or version before networking %j', async (use) => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(sendChat(agentId, conversationId, 'Mensagem', undefined, false, artifact.id, use)).rejects.toMatchObject({ code: 'invalid_request' }); expect(fetcher).not.toHaveBeenCalled()
  })
  it('reads the exact safe metadata linked to the historical user turn', async () => {
    const message = { id: artifact.id, role: 'user', content: 'Mensagem', created_at: artifact.created_at, attachments: [metadata] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ conversation_id: conversationId, messages: [message] })))
    expect((await messages(agentId, conversationId)).messages[0].attachments).toEqual([metadata])
  })
  it.each([{ ...metadata, content: 'raw private text' }, { ...metadata, source: 'VM' }, { ...metadata, size_bytes: 65537 }, { ...metadata, artifact_id: 'wrong' }])('rejects extra or invalid historical attachment metadata %j', async (item) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ conversation_id: conversationId, messages: [{ id: artifact.id, role: 'user', content: 'Mensagem', created_at: artifact.created_at, attachments: [item] }] })))
    await expect(messages(agentId, conversationId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
