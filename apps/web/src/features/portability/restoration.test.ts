import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { checkedDestinations, checkedReport, restoreDestinations, restoreReport, restoreReviews, uploadRestore } from './restoration'
import { MAX_EXPORT_BYTES } from './api'

const reportFixture = { restore_id: '10000000-0000-4000-8000-000000000001', source_id: '20000000-0000-4000-8000-000000000001', destination: 'recuperada', status: 'inactive', created_at: '2026-10-21T12:00:00Z', package_sha256: 'a'.repeat(64), source_schema: 18, destination_schema: 18, records: { agents: 2, messages: 3 }, blobs: 1, changes: [], review_required: [...restoreReviews], source_evidence: 'source.zip', revision_policy: 'new-installation-baseline-original-revisions' }
beforeEach(() => { setCsrfToken('private-csrf') })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); setCsrfToken() })
describe('inactive restoration SDK', () => {
  it('accepts only closed unique operator aliases without paths', () => {
    expect(checkedDestinations([{ id: 'recuperada', available: true, state: 'inactive_only' }])).toHaveLength(1)
    for (const value of [null, {}, [{ id: '../escape', available: true, state: 'inactive_only' }], [{ id: 'ok', available: 'true', state: 'inactive_only' }], [{ id: 'ok', available: true, state: 'active' }], [{ id: 'ok', available: true, state: 'inactive_only', path: '/private' }], Array.from({ length: 11 }, (_, i) => ({ id: `a${i}`, available: true, state: 'inactive_only' })), Array(2).fill({ id: 'duplicate', available: true, state: 'inactive_only' })]) expect(() => checkedDestinations(value)).toThrow('invalid_response')
  })
  it('validates the report against the exact selected destination and inactive contract', () => {
    expect(checkedReport(reportFixture, 'recuperada')).toEqual(reportFixture)
    for (const patch of [{ destination: 'other' }, { status: 'ready' }, { package_sha256: 'bad' }, { restore_id: '0' }, { source_id: '00000000-0000-0000-0000-000000000000' }, { created_at: '2026-10-21' }, { source_schema: 16 }, { records: { agents: -1 } }, { blobs: 1.5 }, { changes: [{}] }, { review_required: ['activate_everything'] }, { review_required: ['new_identity', 'new_identity'] }, { source_evidence: '/private/source.zip' }, { revision_policy: 'overwrite' }, { secret: 'must-not-render' }]) expect(() => checkedReport({ ...reportFixture, ...patch }, 'recuperada')).toThrow('invalid_response')
  })
  it('uses authenticated GET only to list and recover a report', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([{ id: 'recuperada', available: false, state: 'inactive_only' }])).mockResolvedValueOnce(Response.json(reportFixture)); vi.stubGlobal('fetch', fetcher)
    await restoreDestinations(); await restoreReport('recuperada')
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/v1/restoration/destinations', '/api/v1/restoration/destinations/recuperada/report'])
    for (const [, init] of fetcher.mock.calls) expect(init).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error' })
  })
  it('sends the complete inert Blob once with explicit consent and CSRF', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(reportFixture)); vi.stubGlobal('fetch', fetcher)
    const blob = new Blob(['private ZIP bytes'], { type: 'text/plain' })
    await uploadRestore('recuperada', blob, true)
    expect(fetcher).toHaveBeenCalledOnce(); expect(fetcher.mock.calls[0][0]).toBe('/api/v1/restoration/destinations/recuperada/restore')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', body: blob, headers: { 'Content-Type': 'application/zip', 'X-Bees-Restore-Consent': 'restore_inactive_installation', 'X-Bees-CSRF': 'private-csrf' }, credentials: 'same-origin', redirect: 'error', cache: 'no-store' })
  })
  it('blocks missing consent, empty/oversize files, paths and absent CSRF before HTTP', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(uploadRestore('../bad', new Blob(['x']), true)).rejects.toThrow('invalid_request')
    await expect(uploadRestore('ok', new Blob(['x']), false)).rejects.toThrow('invalid_request')
    await expect(uploadRestore('ok', new Blob([]), true)).rejects.toThrow('invalid_request')
    await expect(uploadRestore('ok', { size: MAX_EXPORT_BYTES + 1 } as Blob, true)).rejects.toThrow('invalid_request')
    setCsrfToken(); await expect(uploadRestore('ok', new Blob(['x']), true)).rejects.toThrow('csrf_missing'); expect(fetcher).not.toHaveBeenCalled()
  })
  it('does not retry an ambiguous upload and never exposes response text', async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error('PRIVATE')); vi.stubGlobal('fetch', fetcher)
    await expect(uploadRestore('ok', new Blob(['x']), true)).rejects.toThrow('mutation_network'); expect(fetcher).toHaveBeenCalledOnce()
  })
  it('preserves structured report-not-found and operator configuration errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(Response.json({ error: { code: 'restoration_not_configured', message: 'PRIVATE' } }, { status: 503 })).mockResolvedValueOnce(Response.json({ error: { code: 'restoration_not_found' } }, { status: 404 })))
    await expect(restoreDestinations()).rejects.toMatchObject({ code: 'restoration_not_configured', status: 503 })
    await expect(restoreReport('ok')).rejects.toMatchObject({ code: 'restoration_not_found', status: 404 })
  })
})
