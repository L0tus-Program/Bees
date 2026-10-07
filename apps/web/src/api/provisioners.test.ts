import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import type { PairedHost } from './environments'
import { getProvisioners, revokeProvisioner } from './provisioners'
import type { ProvisionerRecord } from './provisioners'
import { checkedProvisionerSnapshot } from '../features/environments/provisionersState'

const host: PairedHost = {
  host_id: '11111111-1111-4111-8111-111111111111', installation_id: '22222222-2222-4222-8222-222222222222', status: 'active', revision: 2, report_revision: 0,
  fingerprint: 'ABCD-EF01-2345-6789', created_at: '2026-10-07T12:00:00Z', paired_at: '2026-10-07T12:00:00Z', last_seen: null, pairing_expires_at: '2026-10-07T12:15:00Z', diagnostic: null, online: false, confirmable: false, provisionable: false,
}
const item: ProvisionerRecord = { provisioner_id: '33333333-3333-4333-8333-333333333333', installation_id: host.installation_id, host_id: host.host_id, revision: 1, status: 'active' }
const requestId = '44444444-4444-4444-8444-444444444444'
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('human provisioner registry contracts', () => {
  it('uses authenticated read-only scoped pagination and strips internal additions', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ provisioners: [{ ...item, secret: 'must-not-enter-UI', claims: ['internal'], tokens: ['private'] }], has_more: true, next_offset: 101 }))
    vi.stubGlobal('fetch', fetcher)
    expect(await getProvisioners(host, undefined, 100)).toEqual({ items: [item], has_more: true, next_offset: 101 })
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/environments/hosts/${host.host_id}/provisioners?offset=100&limit=100`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { ...item, host_id: '55555555-5555-4555-8555-555555555555' },
    { ...item, installation_id: '55555555-5555-4555-8555-555555555555' },
    { ...item, provisioner_id: 'not-a-uuid' }, { ...item, revision: 0 }, { ...item, revision: 1.5 }, { ...item, status: 'connected' },
  ])('rejects inconsistent binding/status/revision: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ provisioners: [value], has_more: false, next_offset: null })))
    await expect(getProvisioners(host)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { provisioners: [item, item], has_more: false, next_offset: null },
    { provisioners: [], has_more: true, next_offset: 1 },
    { provisioners: [item], has_more: true, next_offset: 100 },
    { provisioners: [item], has_more: false, next_offset: 1 },
  ])('rejects duplicates and incoherent paging: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getProvisioners(host)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects invalid client offset before any network access', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(getProvisioners(host, undefined, -1)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('revokes only the reviewed registration using CSRF, exact revision and stable intent UUID', async () => {
    const revoked = { ...item, status: 'revoked', revision: 2 }
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...revoked, secret: 'not-public' }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    expect(await revokeProvisioner({ ...host, status: 'revoked' }, item, requestId)).toEqual(revoked)
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/environments/hosts/${host.host_id}/provisioners/${item.provisioner_id}/revoke`)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, client_request_id: requestId })
    expect(fetcher.mock.calls[0][1].headers['X-Bees-CSRF']).toBe('test-csrf')
  })
  it('cannot revoke without human session CSRF or with a wrong installation', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(revokeProvisioner(host, item, requestId)).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(revokeProvisioner(host, { ...item, installation_id: requestId }, requestId)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it.each([{ ...item, status: 'active', revision: 2 }, { ...item, status: 'revoked', revision: 1 }, { ...item, provisioner_id: requestId, status: 'revoked', revision: 2 }])('refuses incompatible mutation responses: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf')
    await expect(revokeProvisioner(host, item, requestId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('never repeats an uncertain mutation or a revision conflict automatically', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(Response.json({ error: { code: 'state_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf')
    await expect(revokeProvisioner(host, item, requestId)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    await expect(revokeProvisioner(host, item, requestId)).rejects.toMatchObject({ code: 'state_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('propagates cancellation to an in-flight read without issuing another request', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn((_path: string, options: RequestInit) => new Promise<Response>((_resolve, reject) => options.signal!.addEventListener('abort', () => reject(new Error('aborted')))))
    vi.stubGlobal('fetch', fetcher)
    const pending = getProvisioners(host, controller.signal)
    controller.abort()
    await expect(pending).rejects.toThrow('aborted')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('provisioner canonical revision guards', () => {
  it('rejects stale reads without changing the known revoked snapshot', () => {
    const revoked: ProvisionerRecord = { ...item, status: 'revoked', revision: 2 }
    const known = new Map([[item.provisioner_id, revoked]])
    expect(() => checkedProvisionerSnapshot([item], known)).toThrow('state_conflict')
    expect(() => checkedProvisionerSnapshot([{ ...item, revision: 3 }], known)).toThrow('state_conflict')
    expect(known.get(item.provisioner_id)).toBe(revoked)
    expect(checkedProvisionerSnapshot([revoked], known)).toEqual([revoked])
  })
  it('rejects divergent status at the same revision and accepts a later canonical revocation', () => {
    const known = new Map([[item.provisioner_id, item]])
    expect(() => checkedProvisionerSnapshot([{ ...item, status: 'revoked' }], known)).toThrow('state_conflict')
    expect(checkedProvisionerSnapshot([{ ...item, status: 'revoked', revision: 2 }], known)).toEqual([{ ...item, status: 'revoked', revision: 2 }])
  })
})
