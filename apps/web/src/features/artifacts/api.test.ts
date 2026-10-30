import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadArtifact, getArtifacts } from './api'
import { agentId, artifact, binary, page, taskId } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
function serve(body: unknown) { const fetcher = vi.fn().mockResolvedValue(Response.json(body)); vi.stubGlobal('fetch', fetcher); return fetcher }
describe('task artifact API', () => {
  it('reads only the authenticated task projection without hidden fields', async () => {
    const fetcher = serve(page())
    expect((await getArtifacts(agentId, taskId)).items).toEqual([artifact])
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/agents/${agentId}/tasks/${taskId}/artifacts?offset=0&limit=100`, expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }))
  })
  it.each([
    { task_id: agentId }, { storage_key: 'blob/v1/private' }, { metadata: { private: true } }, { size_bytes: -1 }, { revision: 0 }, { version: 0 }, { sha256: null },
    { name: '../secret.txt' }, { name: ' invisible\u200b.txt' }, { previous_id: artifact.id }, { status: 'active' }, { media_type: 'text/plain; charset=utf-8' }, { updated_at: 'invalid' },
  ])('rejects invalid or foreign metadata %j', async (change) => {
    serve(page([{ ...artifact, ...change }]))
    await expect(getArtifacts(agentId, taskId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('accepts pending metadata and legacy records without pretending they have hashes or lineage', async () => {
    serve(page([{ ...artifact, status: 'draft', sha256: null, series_id: null, run_id: null }]))
    expect((await getArtifacts(agentId, taskId)).items[0].sha256).toBeNull()
  })
  it.each([page([artifact, artifact]), page([artifact], true, 1), { ...page(), token: 'private' }, { ...page(), next_offset: 100 }])('rejects duplicates and incoherent pagination', async (body) => {
    serve(body); await expect(getArtifacts(agentId, taskId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects context before any request', async () => {
    const fetcher = serve(page())
    await expect(getArtifacts('wrong', taskId)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(getArtifacts(agentId, taskId, -1)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).not.toHaveBeenCalled()
  })
})
describe('exact-version authenticated download', () => {
  it('checks bytes and SHA-256 before producing an inert download blob', async () => {
    const fetcher = vi.fn().mockResolvedValue(binary()); vi.stubGlobal('fetch', fetcher)
    const blob = await downloadArtifact(agentId, taskId, artifact)
    expect(await blob.text()).toBe('abc'); expect(blob.type).toBe('application/octet-stream')
    expect(fetcher).toHaveBeenCalledWith(`/api/v1/agents/${agentId}/artifacts/${artifact.id}/download`, expect.objectContaining({ redirect: 'error', credentials: 'same-origin', cache: 'no-store' }))
  })
  it.each(['abcd', 'ab', 'xyz'])('rejects altered, short and excess bytes: %s', async (content) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(binary(content)))
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'artifact_integrity_failed' })
  })
  it.each<Record<string, string>>([{ 'Content-Disposition': 'inline' }, { 'Content-Disposition': `attachment; filename*=UTF-8''other.txt` }, { 'Content-Type': 'text/html' }, { 'Content-Length': '200000000' }, { 'Cache-Control': 'public' }])('rejects response identity and unsafe headers: %j', async (headers) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(binary('abc', headers)))
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('expires the human session on 401 and never returns a blob', async () => {
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'session_expired' } }, { status: 401 })))
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'session_expired', status: 401 })
    expect(dispatchEvent.mock.calls[0][0].type).toBe('bees:session-expired')
  })
  it.each(['artifact_blob_missing', 'artifact_integrity_failed', 'artifact_not_ready'])('preserves sanitized API failure %s', async (code) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code }, path: 'private' }, { status: 409 })))
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code, status: 409 })
  })
  it('rejects foreign, pending and oversized downloads before fetch', async () => {
    const fetcher = serve(page())
    await expect(downloadArtifact(agentId, agentId, artifact)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(downloadArtifact(agentId, taskId, { ...artifact, status: 'draft' })).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(downloadArtifact(agentId, taskId, { ...artifact, size_bytes: 26 * 1024 * 1024 })).rejects.toMatchObject({ code: 'artifact_download_too_large' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('does not return bytes when cancellation arrives during a response', async () => {
    const controller = new AbortController()
    vi.stubGlobal('fetch', vi.fn().mockImplementation(async () => { controller.abort(); return binary() }))
    await expect(downloadArtifact(agentId, taskId, artifact, controller.signal)).rejects.toBeInstanceOf(Error)
  })
})
