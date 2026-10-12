import { ApiError, record, request, setCsrfToken } from '../../api/client'

export interface ArtifactRecord {
  id: string; task_id: string; run_id: string | null; name: string; media_type: string; size_bytes: number; sha256: string | null
  version: number; series_id: string | null; previous_id: string | null; status: 'draft' | 'ready' | 'failed' | 'deleted'
  revision: number; created_at: string; updated_at: string
}
export interface ArtifactPage { items: ArtifactRecord[]; has_more: boolean; next_offset: number | null }
const keys = ['id', 'task_id', 'run_id', 'name', 'media_type', 'size_bytes', 'sha256', 'version', 'series_id', 'previous_id', 'status', 'revision', 'created_at', 'updated_at']
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
const integer = (value: unknown, min: number): value is number => Number.isSafeInteger(value) && (value as number) >= min
const timestamp = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
function checked(value: unknown, taskId: string): ArtifactRecord {
  if (!record(value) || Object.keys(value).length !== keys.length || keys.some((key) => !(key in value))
    || !uuid(value.id) || value.task_id !== taskId || !(value.run_id === null || uuid(value.run_id))
    || typeof value.name !== 'string' || !value.name.trim() || value.name.trim() !== value.name || Array.from(value.name).length > 200 || /[/\\\p{Cc}\p{Cf}]/u.test(value.name)
    || typeof value.media_type !== 'string' || !/^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/.test(value.media_type)
    || !integer(value.size_bytes, 0) || !(value.sha256 === null || typeof value.sha256 === 'string' && /^[a-f0-9]{64}$/.test(value.sha256))
    || !integer(value.version, 1) || !integer(value.revision, 1) || !['draft', 'ready', 'failed', 'deleted'].includes(String(value.status))
    || !(value.series_id === null || uuid(value.series_id)) || !(value.previous_id === null || uuid(value.previous_id))
    || value.previous_id !== null && (value.series_id === null || value.previous_id === value.id || value.version < 2)
    || value.status === 'ready' && value.sha256 === null || !timestamp(value.created_at) || !timestamp(value.updated_at)
    || Date.parse(value.updated_at) < Date.parse(value.created_at)) throw new ApiError('invalid_response')
  return value as unknown as ArtifactRecord
}
export async function getArtifacts(agentId: string, taskId: string, offset = 0, signal?: AbortSignal): Promise<ArtifactPage> {
  if (!uuid(agentId) || !uuid(taskId) || !integer(offset, 0) || offset > 1_000_000) throw new ApiError('invalid_request')
  const value = await request(`/agents/${encodeURIComponent(agentId)}/tasks/${encodeURIComponent(taskId)}/artifacts?offset=${offset}&limit=100`, { authenticated: true, signal })
  if (!record(value) || Object.keys(value).length !== 3 || !Array.isArray(value.artifacts) || value.artifacts.length > 100 || typeof value.has_more !== 'boolean'
    || (value.has_more ? value.artifacts.length !== 100 || value.next_offset !== offset + 100 : value.next_offset !== null)) throw new ApiError('invalid_response')
  const items = value.artifacts.map((item) => checked(item, taskId))
  if (new Set(items.map((item) => item.id)).size !== items.length) throw new ApiError('invalid_response')
  return { items, has_more: value.has_more, next_offset: value.next_offset as number | null }
}

// Bytes are held only for the requested version; they are never rendered as active content.
export async function downloadArtifact(agentId: string, taskId: string, artifact: ArtifactRecord, signal?: AbortSignal): Promise<Blob> {
  if (!uuid(agentId) || !uuid(taskId) || artifact.task_id !== taskId || artifact.status !== 'ready' || checked(artifact, taskId).sha256 === null) throw new ApiError('invalid_request')
  if (artifact.size_bytes > 25 * 1024 * 1024) throw new ApiError('artifact_download_too_large')
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort(signal?.reason)
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 30000)
  try {
    const response = await fetch(`/api/v1/agents/${encodeURIComponent(agentId)}/artifacts/${encodeURIComponent(artifact.id)}/download`, {
      method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
      headers: { Accept: artifact.media_type },
    })
    if (!response.ok) {
      if (response.status === 401) { setCsrfToken(); window.dispatchEvent(new Event('bees:session-expired')) }
      let body: unknown
      try { body = await response.json() } catch { /* Never expose response text. */ }
      const code = record(body) && record(body.error) && typeof body.error.code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(body.error.code) ? body.error.code : `http_${response.status}`
      throw new ApiError(code, response.status)
    }
    const disposition = response.headers.get('content-disposition') ?? ''
    const filename = disposition.match(/filename\*=UTF-8''([^;]+)$/i)?.[1]
    if (!/^attachment;/i.test(disposition) || !filename || decodeURIComponent(filename) !== artifact.name
      || response.headers.get('content-type')?.split(';')[0] !== artifact.media_type
      || response.headers.get('content-length') !== String(artifact.size_bytes)
      || response.headers.get('x-content-type-options') !== 'nosniff' || response.headers.get('cache-control') !== 'no-store') throw new ApiError('invalid_response')
    if (!response.body) throw new ApiError('invalid_response')
    const bodyReader = response.body.getReader()
    const chunks: Uint8Array[] = []
    let received = 0
    try {
      for (;;) {
        const chunk = await bodyReader.read()
        if (chunk.done) break
        received += chunk.value.byteLength
        if (received > artifact.size_bytes) { await bodyReader.cancel(); throw new ApiError('artifact_integrity_failed') }
        chunks.push(chunk.value)
      }
    } finally { bodyReader.releaseLock() }
    if (received !== artifact.size_bytes) throw new ApiError('artifact_integrity_failed')
    const bytes = new Uint8Array(received)
    let cursor = 0
    for (const chunk of chunks) { bytes.set(chunk, cursor); cursor += chunk.byteLength }
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
    if (Array.from(digest, (value) => value.toString(16).padStart(2, '0')).join('') !== artifact.sha256) throw new ApiError('artifact_integrity_failed')
    if (controller.signal.aborted) throw new ApiError('request_timeout')
    // An inert blob prevents a browser from treating downloaded markdown/JSON as a page.
    return new Blob([bytes], { type: 'application/octet-stream' })
  } catch (failure) {
    if (signal?.aborted) throw failure
    if (timedOut) throw new ApiError('request_timeout')
    if (failure instanceof ApiError) throw failure
    throw new ApiError('network_error')
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort) }
}
