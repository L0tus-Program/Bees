import { ApiError, record, request, setCsrfToken } from '../../api/client'

export const MAX_EXPORT_BYTES = 256 * 1024 * 1024
export interface ExportReceipt { id: string; sha256: string; size_bytes: number; rows: number; blobs: number; status: 'ready' }
const count = (value: unknown, max: number): value is number => Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= max
export function checkedExport(value: unknown): ExportReceipt {
  if (!record(value) || Object.keys(value).length !== 6 || !['id', 'sha256', 'size_bytes', 'rows', 'blobs', 'status'].every(key => key in value)
    || typeof value.id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.id) || value.id === '00000000-0000-0000-0000-000000000000'
    || typeof value.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.sha256) || !count(value.size_bytes, MAX_EXPORT_BYTES) || value.size_bytes === 0
    || !count(value.rows, 200000) || !count(value.blobs, 10000) || value.status !== 'ready') throw new ApiError('invalid_response')
  return value as unknown as ExportReceipt
}
export async function createExport(signal?: AbortSignal): Promise<ExportReceipt> {
  return checkedExport(await request('/portability/exports', { body: { consent: 'export_private_data' }, authenticated: true, signal, timeoutMs: 60000 }))
}
export const exportFilename = (receipt: ExportReceipt) => `bees-export-${checkedExport(receipt).id}.zip`

// Only the selected confirmed copy is fetched; ZIP contents are never rendered.
export async function downloadExport(receipt: ExportReceipt, signal?: AbortSignal, onProgress?: (received: number, total: number) => void): Promise<Blob> {
  checkedExport(receipt)
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort(signal?.reason)
  if (signal?.aborted) abort()
  signal?.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 120000)
  try {
    const response = await fetch(`/api/v1/portability/exports/${receipt.id}/download`, { method: 'GET', headers: { Accept: 'application/zip' }, credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
    if (response.status === 401) { setCsrfToken(); if (typeof window !== 'undefined') window.dispatchEvent(new Event('bees:session-expired')) }
    if (!response.ok) throw new ApiError(response.status === 404 ? 'export_missing' : 'export_download_failed', response.status)
    if (response.status !== 200 || response.headers.get('content-type')?.split(';')[0] !== 'application/zip'
      || response.headers.get('content-disposition') !== `attachment; filename="${exportFilename(receipt)}"`
      || response.headers.get('content-length') !== String(receipt.size_bytes) || response.headers.get('cache-control') !== 'no-store'
      || response.headers.get('pragma') !== 'no-cache' || response.headers.get('x-content-type-options') !== 'nosniff' || !response.body) throw new ApiError('invalid_response')
    const reader = response.body.getReader(), chunks: Uint8Array[] = []
    let received = 0
    try {
      for (;;) {
        const chunk = await reader.read()
        if (chunk.done) break
        received += chunk.value.byteLength
        if (received > receipt.size_bytes) { await reader.cancel(); throw new ApiError('export_integrity_failed') }
        chunks.push(chunk.value); onProgress?.(received, receipt.size_bytes)
      }
    } finally { reader.releaseLock() }
    if (received !== receipt.size_bytes) throw new ApiError('export_integrity_failed')
    const bytes = new Uint8Array(received)
    let offset = 0
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
    if (Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('') !== receipt.sha256) throw new ApiError('export_integrity_failed')
    if (controller.signal.aborted) throw new ApiError('request_cancelled')
    return new Blob([bytes], { type: 'application/octet-stream' })
  } catch (error) {
    if (signal?.aborted) throw error
    if (timedOut) throw new ApiError('request_timeout')
    if (error instanceof ApiError) throw error
    throw new ApiError('network_error')
  } finally { clearTimeout(timeout); controller.abort(); signal?.removeEventListener('abort', abort) }
}
