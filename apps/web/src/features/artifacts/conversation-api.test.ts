import { afterEach, describe, expect, it, vi } from 'vitest'
import { getConversationArtifact, getConversationArtifacts, previewArtifact } from './api'
import { agentId, artifact, conversationId, page } from './fixtures'
afterEach(() => vi.unstubAllGlobals())
const serve = (value: unknown) => { const fetcher = vi.fn().mockResolvedValue(Response.json(value)); vi.stubGlobal('fetch', fetcher); return fetcher }
describe('conversation-bound closed artifact DTOs', () => {
  it('preserves legacy task links while binding conversation and agent explicitly', async () => {
    serve(page()); expect((await getConversationArtifacts(agentId, conversationId)).items).toEqual([artifact])
    serve(artifact); expect(await getConversationArtifact(agentId, conversationId, artifact.id)).toEqual(artifact)
  })
  it.each([{ agent_id: artifact.id }, { conversation_id: artifact.id }, { conversation_id: null }, { source: 'model_generated' }, { source: null }, { file_path: 'private' }, { task_id: null, run_id: artifact.run_id }, { name: 'line\u2028.txt' }])('rejects extra, wrong-context and inferred provenance %j', async (change) => {
    serve(page([{ ...artifact, ...change }])); await expect(getConversationArtifacts(agentId, conversationId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('refuses a different artifact ID during reconciliation', async () => {
    serve({ ...artifact, id: agentId }); await expect(getConversationArtifact(agentId, conversationId, artifact.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('supports taskless human uploads but not records with neither owning context', async () => {
    serve(page([{ ...artifact, task_id: null, run_id: null, source: 'user_upload' }])); expect((await getConversationArtifacts(agentId, conversationId)).items[0].task_id).toBeNull()
    serve(page([{ ...artifact, task_id: null, run_id: null, conversation_id: null }])); await expect(getConversationArtifacts(agentId, conversationId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
describe('verified plain text preview', () => {
  const preview = { artifact, text: 'abc', truncated: false, total_bytes: 3 }
  it('verifies complete bytes and hash, requests only the safe bounded projection', async () => {
    const fetcher = serve(preview); expect((await previewArtifact(agentId, conversationId, artifact)).text).toBe('abc')
    expect(fetcher.mock.calls[0][0]).toContain(`/conversations/${conversationId}/artifacts/${artifact.id}/preview?limit=65536`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: 'same-origin', cache: 'no-store', redirect: 'error' })
  })
  it.each([{ text: 'xyz' }, { text: 'ab' }, { text: 'abc\0' }, { truncated: true }, { total_bytes: 4 }, { artifact: { ...artifact, revision: 1 } }, { artifact: { ...artifact, source: 'user_revision' } }, { path: 'private' }])('rejects incomplete, rewritten or additional preview data %j', async (change) => {
    serve({ ...preview, ...change }); await expect(previewArtifact(agentId, conversationId, artifact)).rejects.toBeInstanceOf(Error)
  })
  it('accepts an explicitly truncated prefix without pretending it is the whole editable file', async () => {
    const large = { ...artifact, size_bytes: 70000 }; serve({ artifact: large, text: 'a'.repeat(65536), truncated: true, total_bytes: 70000 })
    expect((await previewArtifact(agentId, conversationId, large)).truncated).toBe(true)
    serve({ artifact: large, text: 'a'.repeat(65537), truncated: true, total_bytes: 70000 }); await expect(previewArtifact(agentId, conversationId, large)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
