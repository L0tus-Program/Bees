import { createHash, webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { checkedExport, createExport, downloadExport, MAX_EXPORT_BYTES } from './api'
import { clearExportState, EXPORT_STATE_KEY, rememberExportPending, rememberExportReceipt, restoreExportState } from './session'

const bytes = new TextEncoder().encode('private ZIP fixture bytes')
export const receipt = { id: '10000000-0000-4000-8000-000000000001', sha256: createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.byteLength, rows: 12, blobs: 1, status: 'ready' as const }
const headers = { 'Content-Type': 'application/zip', 'Content-Length': String(bytes.byteLength), 'Content-Disposition': `attachment; filename="bees-export-${receipt.id}.zip"`, 'Cache-Control': 'no-store', Pragma: 'no-cache', 'X-Content-Type-Options': 'nosniff' }
function response(body: Uint8Array = bytes, changes: Record<string, string> = {}) { return new Response(body.slice().buffer, { headers: { ...headers, ...changes } }) }
let storage: Map<string, string>
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('test-csrf'); storage = new Map(); vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => { storage.set(key, value) }, removeItem: (key: string) => { storage.delete(key) } }) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })

describe('private export boundary', () => {
  it('creates exactly one copy only with closed consent and human CSRF, without downloading automatically', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
    expect(await createExport()).toEqual(receipt)
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/portability/exports')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { 'X-Bees-CSRF': 'test-csrf' } })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ consent: 'export_private_data' })
    setCsrfToken(); await expect(createExport()).rejects.toMatchObject({ code: 'csrf_missing' }); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([
    { id: '../private' }, { id: '00000000-0000-0000-0000-000000000000' }, { status: 'failed' }, { sha256: 'x'.repeat(64) },
    { size_bytes: 0 }, { size_bytes: MAX_EXPORT_BYTES + 1 }, { rows: -1 }, { blobs: 10001 }, { password: 'PRIVATE_CONTENT' },
  ])('refuses unsupported metadata before a download: %j', async (change) => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(downloadExport({ ...receipt, ...change } as typeof receipt)).rejects.toMatchObject({ code: 'invalid_response' }); expect(fetcher).not.toHaveBeenCalled()
    expect(() => checkedExport({ ...receipt, ...change })).toThrow('invalid_response')
  })
  it('streams the exact confirmed copy, verifies SHA-256, and produces only an inert blob', async () => {
    const fetcher = vi.fn().mockResolvedValue(response()), progress = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const blob = await downloadExport(receipt, undefined, progress)
    expect(blob.type).toBe('application/octet-stream'); expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes)
    expect(progress).toHaveBeenLastCalledWith(bytes.byteLength, bytes.byteLength)
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/portability/exports/${receipt.id}/download`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error' }); expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { 'Content-Type': 'text/html' }, { 'Content-Length': '1' }, { 'Content-Disposition': 'attachment; filename="private.zip"' },
    { 'Cache-Control': 'public' }, { Pragma: 'cache' }, { 'X-Content-Type-Options': '' },
  ] as Record<string, string>[])('refuses header or binding mismatch without accepting a download: %j', async (change) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response(bytes, change)))
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('refuses equal-sized adulteration and truncated bytes', async () => {
    const wrong = bytes.slice(); wrong[0] ^= 1
    const fetcher = vi.fn().mockResolvedValueOnce(response(wrong)).mockResolvedValueOnce(response(bytes.slice(1))); vi.stubGlobal('fetch', fetcher)
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'export_integrity_failed' })
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'export_integrity_failed' })
  })
  it('cancels a stream as soon as received bytes exceed the confirmed size', async () => {
    const cancelled = vi.fn(), body = new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array(bytes.byteLength + 1)) }, cancel: cancelled })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body, { headers })))
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'export_integrity_failed' }); expect(cancelled).toHaveBeenCalledOnce()
  })
  it('uses an absolute timeout and never retries a download', async () => {
    vi.useFakeTimers(); const fetcher = vi.fn((_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }))); vi.stubGlobal('fetch', fetcher)
    const request = expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'request_timeout' })
    await vi.advanceTimersByTimeAsync(120000); await request; expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('reports a missing copy and clears human CSRF on session expiry without replaying creation', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(new Response('', { status: 404 })).mockResolvedValueOnce(new Response('', { status: 401 })); vi.stubGlobal('fetch', fetcher)
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'export_missing' })
    await expect(downloadExport(receipt)).rejects.toMatchObject({ code: 'export_download_failed', status: 401 })
    await expect(createExport()).rejects.toMatchObject({ code: 'csrf_missing' }); expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
describe('reference-only tab recovery', () => {
  it('preserves a known copy while a new creation is uncertain and never stores ZIP contents or tokens', () => {
    expect(restoreExportState()).toEqual({ status: 'empty' }); rememberExportReceipt(receipt); expect(restoreExportState()).toEqual({ status: 'ready', receipt })
    rememberExportPending(receipt); expect(restoreExportState()).toEqual({ status: 'pending', previous: receipt })
    const raw = storage.get(EXPORT_STATE_KEY)!; expect(raw).not.toContain('private ZIP'); expect(raw).not.toContain('test-csrf')
    clearExportState(); expect(restoreExportState()).toEqual({ status: 'empty' })
  })
  it('distinguishes corrupt/unavailable storage and refuses silent persistence failure', () => {
    storage.set(EXPORT_STATE_KEY, JSON.stringify({ version: 1, status: 'ready', receipt: { ...receipt, endpoint: 'https://private.invalid' } })); expect(restoreExportState()).toEqual({ status: 'invalid' })
    storage.set(EXPORT_STATE_KEY, '{'); expect(restoreExportState()).toEqual({ status: 'invalid' }); storage.clear()
    vi.stubGlobal('sessionStorage', { getItem: () => null, setItem: () => {} }); expect(() => rememberExportPending(null)).toThrow('export_state_unavailable')
    vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('blocked') } }); expect(restoreExportState()).toEqual({ status: 'unavailable' })
  })
})
