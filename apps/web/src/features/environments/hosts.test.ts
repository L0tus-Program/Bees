import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { confirmHostPair, getPairedHosts, revokeHostPair } from '../../api/environments'
import type { PairedHost } from '../../api/environments'
import { hostStatusObserver, loadPairedHosts, pairedHostStatus, readInstallationHost, sameHostAuthority } from './hosts'

const now = Date.parse('2026-10-06T18:00:00Z')
const pending: PairedHost = {
  host_id: 'edcbab19-f977-408b-a4d9-b41d6ee4d85a', installation_id: 'e1d1c19f-48a0-4c94-ad30-ac4b833691ad',
  status: 'pending', revision: 1, report_revision: 0, fingerprint: 'ABCD-EF01-2345-6789', created_at: new Date(now - 1000).toISOString(), paired_at: null,
  pairing_expires_at: new Date(now + 900_000).toISOString(), last_seen: null, diagnostic: null, online: false, confirmable: true, provisionable: false,
}
const active: PairedHost = { ...pending, status: 'active', revision: 2, report_revision: 1, paired_at: new Date(now).toISOString(), last_seen: new Date(now).toISOString(),
  diagnostic: { driver: 'hyperv', status: 'driver_unavailable', probe: { platform: true, module: false, service: false }, provisionable: false }, online: true, confirmable: false }
const revoked: PairedHost = { ...active, status: 'revoked', revision: 3, online: false }
const page = (hosts: unknown[], hasMore = false, next: number | null = null) => ({ hosts, has_more: hasMore, next_offset: next })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('host diagnostic API boundaries', () => {
  it('reads only public diagnostic data and discards transport additions', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(page([{ ...pending, private_transport: 'never form state' }]))); vi.stubGlobal('fetch', fetcher)
    const hosts = await getPairedHosts()
    expect(hosts.items).toEqual([pending]); expect(hosts.items[0]).not.toHaveProperty('private_transport')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it('confirms diagnostic-only pairing with exact fingerprint, human revision, UUID and session CSRF', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(active)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    expect((await confirmHostPair(pending, 'request-1')).provisionable).toBe(false)
    expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/environments/hosts/${pending.host_id}/confirm`)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', headers: { 'X-Bees-CSRF': 'csrf-test' } })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, client_request_id: 'request-1', fingerprint: pending.fingerprint })
  })
  it('revokes a specific host with human CAS without reporting or forwarding capabilities', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(revoked)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    expect((await revokeHostPair(active, 'revoke-1')).status).toBe('revoked')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 2, client_request_id: 'revoke-1' })
  })
  it('does not write without a human session CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(confirmHostPair(pending, 'request-1')).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(revokeHostPair(active, 'request-2')).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('never repeats an uncertain or conflicting human decision', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost')).mockResolvedValueOnce(Response.json({ error: { code: 'host_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(confirmHostPair(pending, 'request-1')).rejects.toMatchObject({ code: 'mutation_network' })
    await expect(revokeHostPair(active, 'request-2')).rejects.toMatchObject({ code: 'host_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it.each([
    { ...active, host_id: 'b7ee3380-8221-4bce-92a5-387d32b800e9' }, { ...active, installation_id: 'ba869eb3-2816-4b7f-9d75-351b167630fc' },
    { ...active, fingerprint: '1111-2222-3333-4444' }, { ...active, revision: 0 }, { ...active, status: 'revoked', online: false },
    { ...active, usable: true, provisionable: true }, { ...active, created_at: new Date(now).toISOString() },
  ])('rejects confirmation responses that switch identity or overstate authority: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf-test')
    await expect(confirmHostPair(pending, 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { ...pending, online: true }, { ...pending, diagnostic: active.diagnostic }, { ...active, paired_at: null }, { ...active, last_seen: null },
    { ...active, diagnostic: { ...active.diagnostic, probe: { platform: true, module: 'yes', service: false } } },
    { ...active, diagnostic: { ...active.diagnostic, probe: { ...active.diagnostic?.probe, hostname: 'personal machine' } } },
    { ...active, diagnostic: { ...active.diagnostic, provisionable: true } }, { ...revoked, online: true }, { ...active, confirmable: true },
    { ...pending, fingerprint: 'ABCD-EF01' }, { ...pending, report_revision: -1 },
  ])('rejects unsafe or inconsistent diagnostic projections: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([value]))))
    await expect(getPairedHosts()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('accepts real libvirt booleans without treating them as provisioning capability', async () => {
    const linux = { ...active, diagnostic: { driver: 'libvirt', status: 'guest_bridge_required', probe: { platform: true, kvm: true, service: true }, provisionable: false } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([linux]))))
    expect((await getPairedHosts()).items[0].provisionable).toBe(false)
  })
  it.each([page([pending, pending]), page([pending], true, 0), page([], true, 100), page([pending, { ...active, host_id: 'b7ee3380-8221-4bce-92a5-387d32b800e9', installation_id: 'ba869eb3-2816-4b7f-9d75-351b167630fc' }])])('rejects ambiguous host pages: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getPairedHosts()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects invalid offsets before contacting any host endpoint', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(getPairedHosts(undefined, -1)).rejects.toMatchObject({ code: 'invalid_request' }); expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('host freshness and reconciliation', () => {
  it('never labels an old report as online, even when an old response still contains online true', () => {
    expect(pairedHostStatus(active, now + 59_000)).toBe('active')
    expect(pairedHostStatus(active, now + 61_000)).toBe('offline')
    expect(pairedHostStatus({ ...active, online: false }, now)).toBe('offline')
    expect(pairedHostStatus({ ...active, diagnostic: null, last_seen: null, online: false }, now)).toBe('offline')
  })
  it('blocks local expired pairing and server-declared unconfirmable pairing', () => {
    expect(pairedHostStatus(pending, now)).toBe('pending')
    expect(pairedHostStatus(pending, now + 900_000)).toBe('expired')
    expect(pairedHostStatus({ ...pending, confirmable: false }, now)).toBe('expired')
  })
  it('preserves revoked status regardless of cached diagnostic age', () => { expect(pairedHostStatus(revoked, now)).toBe('revoked') })
  it('allows report-only changes without silently confirming changed human authority', () => {
    expect(sameHostAuthority(active, { ...active, report_revision: 2, last_seen: new Date(now + 1000).toISOString() })).toBe(true)
    expect(sameHostAuthority(active, revoked)).toBe(false)
    expect(sameHostAuthority(pending, { ...pending, fingerprint: '1111-2222-3333-4444' })).toBe(false)
  })
  it('loads all public hosts using GET only', async () => {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.includes('offset=1') ? page([{ ...revoked, host_id: 'b7ee3380-8221-4bce-92a5-387d32b800e9' }]) : page([pending], true, 1))))
    vi.stubGlobal('fetch', fetcher); expect(await loadPairedHosts()).toHaveLength(2)
    expect(fetcher.mock.calls.every((call) => call[1].method === 'GET')).toBe(true)
  })
  it('rejects mixed installations or duplicate hosts across pages during concurrent changes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.includes('offset=1') ? page([pending]) : page([pending], true, 1)))))
    await expect(loadPairedHosts()).rejects.toMatchObject({ code: 'state_conflict' })
  })
  it('updates installation diagnostics on the first report and TTL expiry without rereading VM requests for heartbeats', async () => {
    const absent = { driver: 'none', status: 'host_setup_required', probe: null, provisionable: false }
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ templates: [], host: absent }))
      .mockResolvedValueOnce(Response.json({ templates: [], host: active.diagnostic }))
      .mockResolvedValueOnce(Response.json({ templates: [], host: absent }))
    vi.stubGlobal('fetch', fetcher)
    const observe = hostStatusObserver(); let refresh: Promise<unknown> | null = null
    const changed = () => { refresh = readInstallationHost() }
    const waitingReport: PairedHost = { ...active, last_seen: null, diagnostic: null, online: false, report_revision: 0 }
    observe([waitingReport], now, false, changed); expect(await refresh).toEqual(absent)
    // The human confirmation already succeeded, but the first helper report arrives in the following poll.
    observe([active], now + 1000, false, changed); expect(await refresh).toEqual(active.diagnostic)
    expect(fetcher).toHaveBeenCalledTimes(2)
    const heartbeat = { ...active, report_revision: 2, last_seen: new Date(now + 10_000).toISOString() }
    observe([heartbeat], now + 10_000, false, changed); expect(fetcher).toHaveBeenCalledTimes(2)
    observe([heartbeat], now + 69_999, false, changed); expect(fetcher).toHaveBeenCalledTimes(2)
    observe([heartbeat], now + 70_000, false, changed); expect(await refresh).toEqual(absent)
    observe([heartbeat], now + 71_000, false, changed); expect(fetcher).toHaveBeenCalledTimes(3)
    expect(fetcher.mock.calls.every((call) => call[0] === '/api/v1/environments/catalog' && call[1].method === 'GET')).toBe(true)
  })
  it('does not refetch when a parent rerender changes callback identity; refreshes meaningful diagnostic changes and revocation', () => {
    const observe = hostStatusObserver(); const changed = vi.fn()
    observe([active], now, false, () => changed())
    observe([active], now + 1000, false, () => changed())
    expect(changed).toHaveBeenCalledTimes(1)
    const capable: PairedHost = { ...active, diagnostic: { ...active.diagnostic!, status: 'guest_bridge_required', probe: { platform: true, module: true, service: true } } }
    observe([capable], now + 1000, false, () => changed()); expect(changed).toHaveBeenCalledTimes(2)
    observe([{ ...capable, report_revision: 3, last_seen: new Date(now + 2000).toISOString() }], now + 2000, false, () => changed())
    expect(changed).toHaveBeenCalledTimes(2)
    observe([revoked], now + 3000, false, () => changed()); expect(changed).toHaveBeenCalledTimes(3)
  })
})
