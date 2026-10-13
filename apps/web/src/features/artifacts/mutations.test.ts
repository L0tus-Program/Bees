import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { agentId, artifact, conversationId } from './fixtures'
import { checkedReceipt, isAttachmentSelection, MAX_FILE_BYTES, prepareBytes, prepareFile, Publication, rememberPublication, restorePublication, validName } from './mutations'
import type { ArtifactIntent } from './mutations'

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
const content = () => prepareBytes('notes.txt', 'text/plain', new TextEncoder().encode('abc'))
const receipt = (intent: ArtifactIntent, changes = {}) => ({ ...artifact, id: intent.id, name: intent.name, media_type: intent.media_type, source: intent.previous ? 'user_revision' as const : 'user_upload' as const, task_id: intent.previous?.task_id ?? null, run_id: null, previous_id: intent.previous?.id ?? null, version: intent.previous ? intent.previous.version + 1 : 1, series_id: intent.previous ? intent.previous.series_id ?? intent.previous.id : intent.id, ...changes })
const command = async () => { setCsrfToken('test-csrf'); const publisher = new Publication(agentId, conversationId, vi.fn()); publisher.choose(await content(), null); return publisher }

describe('human file preparation', () => {
  it('computes exact bytes and hash before any network operation', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    expect(await content()).toMatchObject({ size_bytes: 3, sha256: artifact.sha256 })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it.each(['invisible\u2028.txt', 'break\u2029.txt', 'zero\u200b.txt', '../a.txt', 'a\\b.txt', ' space.txt'])('rejects unsafe name %s', (name) => expect(validName(name)).toBe(false))
  it('rejects invalid UTF8, NUL, invalid JSON and excess size before reading huge file', async () => {
    await expect(prepareBytes('a.txt', 'text/plain', new Uint8Array([255]))).rejects.toMatchObject({ code: 'artifact_invalid_content' })
    await expect(prepareBytes('a.txt', 'text/plain', new Uint8Array([0]))).rejects.toMatchObject({ code: 'artifact_invalid_content' })
    await expect(prepareBytes('a.json', 'application/json', new TextEncoder().encode('{"a":NaN}'))).rejects.toMatchObject({ code: 'artifact_invalid_content' })
    const arrayBuffer = vi.fn(); await expect(prepareFile({ name: 'a.txt', size: MAX_FILE_BYTES + 1, arrayBuffer } as unknown as File)).rejects.toMatchObject({ code: 'artifact_upload_too_large' }); expect(arrayBuffer).not.toHaveBeenCalled()
  })
  it('ignores browser MIME and uses the supported file extension', async () => {
    expect((await prepareFile(new File(['abc'], 'notes.MD', { type: 'text/html' }))).media_type).toBe('text/markdown')
    await expect(prepareFile(new File(['abc'], 'notes.html', { type: 'text/plain' }))).rejects.toMatchObject({ code: 'artifact_invalid_format' })
  })
  it('fences cancelled preparation after hashing', async () => {
    const controller = new AbortController(); controller.abort()
    await expect(prepareBytes('a.txt', 'text/plain', new Uint8Array([97]), controller.signal)).rejects.toMatchObject({ code: 'request_cancelled' })
  })
})
describe('durable publication identity and replay', () => {
  it('sends binary content with CSRF and bound closed header metadata', async () => {
    const publisher = await command(); const intent = publisher.state.intent!
    const fetcher = vi.fn().mockResolvedValue(Response.json(receipt(intent))); vi.stubGlobal('fetch', fetcher)
    await publisher.send()
    expect(publisher.state.phase).toBe('confirmed')
    const options = fetcher.mock.calls[0][1]
    expect(fetcher.mock.calls[0][0]).toContain(`/${intent.id}/content`)
    expect(options).toMatchObject({ method: 'PUT', credentials: 'same-origin', redirect: 'error', headers: { 'X-Bees-CSRF': 'test-csrf', 'Content-Type': 'application/octet-stream' } })
    expect(await options.body.text()).toBe('abc')
    const encoded = options.headers['X-Bees-Artifact']; const decoded = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(encoded.replaceAll('-', '+').replaceAll('_', '/')), (char) => char.charCodeAt(0))))
    expect(decoded).toEqual({ name: 'notes.txt', media_type: 'text/plain', size_bytes: 3, sha256: artifact.sha256, previous_id: null, expected_revision: null })
  })
  it('lost response reconciles committed receipt without republishing', async () => {
    const publisher = await command(); const intent = publisher.state.intent!
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('lost after commit')).mockResolvedValueOnce(Response.json(receipt(intent))); vi.stubGlobal('fetch', fetcher)
    await publisher.send(); expect(publisher.state.phase).toBe('uncertain')
    await publisher.send(); expect(fetcher).toHaveBeenCalledTimes(1)
    await publisher.reconcile(); expect(publisher.state.phase).toBe('confirmed'); expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls[1][1].method).toBe('GET')
  })
  it('missing receipt permits explicit resend with identical UUID and snapshot', async () => {
    const publisher = await command(); const intent = publisher.state.intent!
    const fetcher = vi.fn().mockRejectedValueOnce(new Error()).mockResolvedValueOnce(Response.json({ error: { code: 'not_found' } }, { status: 404 })).mockResolvedValueOnce(Response.json(receipt(intent))); vi.stubGlobal('fetch', fetcher)
    await publisher.send(); await publisher.reconcile(); expect(publisher.state.phase).toBe('retryable')
    await publisher.send(); expect(publisher.state.phase).toBe('confirmed'); expect(fetcher.mock.calls[0][0]).toBe(fetcher.mock.calls[2][0]); expect(fetcher.mock.calls[0][1].headers).toEqual(fetcher.mock.calls[2][1].headers)
  })
  it('draft receipt permits only same-request completion after human reconciliation', async () => {
    const publisher = await command(); const intent = publisher.state.intent!
    const fetcher = vi.fn().mockRejectedValueOnce(new Error()).mockResolvedValueOnce(Response.json(receipt(intent, { status: 'draft', revision: 1 }))).mockResolvedValueOnce(Response.json(receipt(intent))); vi.stubGlobal('fetch', fetcher)
    await publisher.send(); await publisher.reconcile(); expect(publisher.state.phase).toBe('retryable'); expect(publisher.state.artifact?.status).toBe('draft')
    await publisher.send(); expect(publisher.state.phase).toBe('confirmed')
  })
  it('conflict preserves candidate and requires a new human confirmation without automatic rebase', async () => {
    const publisher = await command(); const candidate = publisher.state.candidate!
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'artifact_version_conflict' } }, { status: 409 })))
    await publisher.send(); expect(publisher.state.phase).toBe('conflict'); expect(publisher.state.candidate).toBe(candidate)
    const previousId = publisher.state.intent!.id
    publisher.choose(candidate, artifact); expect(publisher.state.intent?.id).not.toBe(previousId); expect(publisher.state.intent?.previous).toEqual(artifact); expect(publisher.state.phase).toBe('idle')
  })
  it.each([413, 422])('deterministic refusal %s permits changing the candidate', async (status) => {
    const publisher = await command(); vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'artifact_invalid_content' } }, { status })))
    await publisher.send(); expect(publisher.state.phase).toBe('rejected'); expect(publisher.state.artifact).toBeNull()
    publisher.choose(await content(), null); expect(publisher.state.phase).toBe('idle')
  })
  it.each(['artifact_content_invalid', 'artifact_name_invalid', 'artifact_media_type_unsupported', 'artifact_too_large', 'artifact_upload_busy'])('sanitized pre-publication validation %s is a definite refusal even under409', async (code) => {
    const publisher = await command(); const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code } }, { status: 409 })); vi.stubGlobal('fetch', fetcher)
    await publisher.send(); expect(publisher.state.phase).toBe('rejected'); expect(publisher.state.candidate).not.toBeNull(); expect(publisher.state.artifact).toBeNull()
    publisher.choose(await content(), null); expect(publisher.state.phase).toBe('idle'); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('cancellation and late success never claim rollback or confirmed publication', async () => {
    const publisher = await command(); const intent = publisher.state.intent!; let resolve!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise<Response>((done) => { resolve = done })))
    const pending = publisher.send(); publisher.cancelTransport(); expect(publisher.state.phase).toBe('uncertain')
    resolve(Response.json(receipt(intent))); await pending; expect(publisher.state.artifact).toBeNull(); expect(publisher.state.phase).toBe('uncertain')
  })
  it('leaving the context fences late replies and does not publish new state', async () => {
    const publisher = await command(); const intent = publisher.state.intent!; let resolve!: (response: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockImplementation(() => new Promise<Response>((done) => { resolve = done })))
    const pending = publisher.send(); publisher.stop(); resolve(Response.json(receipt(intent))); await pending; expect(publisher.state.artifact).toBeNull()
  })
  it('restores public intent only; reselected bytes must match its immutable fingerprint', async () => {
    const storage = new Map<string, string>(); vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key), setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) })
    const publisher = await command(); vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error())); await publisher.send(); rememberPublication(publisher.state)
    const stored = [...storage.values()][0]; expect(JSON.parse(stored)).not.toHaveProperty('bytes'); expect(stored).not.toContain('"abc"')
    const restored = restorePublication(agentId, conversationId)!; expect(restored).toEqual(publisher.state.intent); expect(restorePublication(agentId, artifact.id)).toBeNull()
    const next = new Publication(agentId, conversationId, vi.fn()); next.restore(restored); expect(next.state.candidate).toBeNull()
    expect(() => next.reselect({ ...publisher.state.candidate!, name: 'other.txt' })).toThrow('artifact_candidate_mismatch')
    next.reselect(publisher.state.candidate!); expect(next.state.candidate?.size_bytes).toBe(3)
  })
})
describe('closed canonical receipts and context selection', () => {
  it.each([{ source: 'system_generated' }, { conversation_id: artifact.id }, { agent_id: artifact.id }, { size_bytes: 4 }, { sha256: 'a'.repeat(64) }, { series_id: artifact.id }, { run_id: artifact.run_id }, { task_id: artifact.task_id }])('refuses misleading publication metadata %j', async (change) => {
    const publisher = await command(); const intent = publisher.state.intent!
    expect(() => checkedReceipt(intent, receipt(intent, change))).toThrow('invalid_response')
  })
  it('preserves lineage for legacy series without a recorded series ID', async () => {
    const publisher = await command(); publisher.clear(); publisher.choose(await content(), { ...artifact, series_id: null }); const intent = publisher.state.intent!
    expect(checkedReceipt(intent, receipt(intent)).series_id).toBe(artifact.id)
  })
  it('limits explicit material to four complete small files with correct binding', () => {
    expect(isAttachmentSelection([artifact], agentId, conversationId)).toBe(true)
    expect(isAttachmentSelection([artifact, artifact], agentId, conversationId)).toBe(false)
    expect(isAttachmentSelection([{ ...artifact, size_bytes: 65537 }], agentId, conversationId)).toBe(false)
    expect(isAttachmentSelection([{ ...artifact, status: 'draft' }], agentId, conversationId)).toBe(false)
    expect(isAttachmentSelection([artifact], artifact.id, conversationId)).toBe(false)
    expect(isAttachmentSelection(Array.from({ length: 5 }, (_, i) => ({ ...artifact, id: `${artifact.id.slice(0, -1)}${i}` })), agentId, conversationId)).toBe(false)
  })
})
