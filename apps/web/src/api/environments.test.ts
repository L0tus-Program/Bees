import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, setCsrfToken } from './client'
import { cancelEnvironment, createEnvironment, getEnvironmentCatalog, getEnvironments } from './environments'
import type { EnvironmentPlan, EnvironmentRecord, EnvironmentTemplate } from './environments'
import { loadEnvironments, planIdentity, resourceProfile, uncertainEnvironmentMutation, validPlan } from '../features/environments/state'

const stamp = '2026-10-06T18:00:00Z'
const host = { driver: 'none', status: 'host_setup_required', provisionable: false, probe: null }
const template: EnvironmentTemplate = {
  id: 'linux-desktop-v1', name: 'Computador Linux', version: 1, os: 'linux', applications: ['chromium', 'libreoffice'], status: 'planned', provisionable: false,
  defaults: { cpu_count: 2, memory_mib: 4096, disk_gib: 30 }, limits: { cpu_count: { min: 1, max: 4 }, memory_mib: { min: 2048, max: 8192 }, disk_gib: { min: 20, max: 100 } },
}
const plan: EnvironmentPlan = { name: 'Computador de teste', template_id: template.id, ...template.defaults }
const environment: EnvironmentRecord = { ...plan, id: 'environment-1', agent_id: 'bee-1', status: 'awaiting_host', revision: 1, created_at: stamp, updated_at: stamp, reason_code: 'host_setup_required', usable: false }
const page = (environments: unknown[], hasMore = false, next: number | null = null) => ({ environments, has_more: hasMore, next_offset: next })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('computer planning API', () => {
  it('reads hardware provisioning progress without claiming the computer is available', async () => {
    const preparing = { ...environment, status: 'provisioning', revision: 2, reason_code: 'hardware_provisioning' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([preparing]))))
    expect((await getEnvironments('bee-1')).items[0]).toEqual(preparing)
  })
  it('rejects a provisioning state that claims the computer is already usable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([{ ...environment, status: 'provisioning', usable: true }]))))
    await expect(getEnvironments('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('does not cancel a request whose hardware provisioning already started', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(cancelEnvironment('bee-1', { ...environment, status: 'provisioning' }, 'cancel-1')).rejects.toMatchObject({ code: 'environment_conflict' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('reads catalogue and status without provisioning or granting any access', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ templates: [template], host })).mockResolvedValueOnce(Response.json(page([environment])))
    vi.stubGlobal('fetch', fetcher)
    expect((await getEnvironmentCatalog()).host.provisionable).toBe(false)
    expect((await getEnvironments('bee-1')).items[0].usable).toBe(false)
    expect(fetcher.mock.calls.every((call) => call[1].method === 'GET' && !call[1].body && call[1].credentials === 'same-origin')).toBe(true)
  })
  it('registers a plan with session CSRF and a human request UUID, without host configuration', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(environment)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    expect(await createEnvironment('bee-1', plan, 'request-1')).toEqual(environment)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', headers: { 'X-Bees-CSRF': 'csrf-test' } })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ ...plan, client_request_id: 'request-1' })
    expect(fetcher.mock.calls[0][1].body).not.toContain('driver')
  })
  it('cancels only the selected bee request using CAS and a request UUID', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...environment, status: 'cancelled', revision: 2 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    expect((await cancelEnvironment('bee-1', environment, 'cancel-1')).status).toBe('cancelled')
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/environments/environment-1/cancel')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, client_request_id: 'cancel-1' })
  })
  it('does not send writes without CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(createEnvironment('bee-1', plan, 'request-1')).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(cancelEnvironment('bee-1', environment, 'cancel-1')).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('does not retry a conflict or unknown mutation outcome automatically', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ error: { code: 'environment_conflict' } }, { status: 409 })).mockRejectedValueOnce(new TypeError('Network lost'))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(cancelEnvironment('bee-1', environment, 'cancel-1')).rejects.toMatchObject({ status: 409 })
    await expect(createEnvironment('bee-1', plan, 'request-1')).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('accepts a replay of a plan cancelled afterwards without claiming a VM is usable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...environment, status: 'cancelled', revision: 2 }))); setCsrfToken('csrf-test')
    expect((await createEnvironment('bee-1', plan, 'request-1')).usable).toBe(false)
  })
  it.each([
    { ...environment, agent_id: 'other' }, { ...environment, name: 'Other name' }, { ...environment, template_id: 'other' },
    { ...environment, cpu_count: 4 }, { ...environment, memory_mib: 8192 }, { ...environment, disk_gib: 100 },
    { ...environment, usable: true }, { ...environment, status: 'ready' }, { ...environment, revision: 0 },
  ])('rejects creation responses that redirect or overstate the plan: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf-test')
    await expect(createEnvironment('bee-1', plan, 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { ...environment, status: 'awaiting_host', revision: 2 }, { ...environment, status: 'cancelled', id: 'other' },
    { ...environment, status: 'cancelled', agent_id: 'other' }, { ...environment, status: 'cancelled', revision: 0 },
    { ...environment, status: 'cancelled', cpu_count: 4 },
  ])('rejects cancellation responses that do not match the intended request: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf-test')
    await expect(cancelEnvironment('bee-1', environment, 'cancel-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    page([environment, environment]), page([environment], true, 0), page([], true, 100),
    page([{ ...environment, agent_id: 'other' }]), page([{ ...environment, usable: true }]), page([{ ...environment, cpu_count: 1.5 }]),
  ])('rejects mixed ownership and malformed pagination: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getEnvironments('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { templates: [template], host: { ...host, provisionable: true } }, { templates: [template], host: { ...host, driver: 'shell' } },
    { templates: [template, template], host }, { templates: [{ ...template, provisionable: true }], host },
    { templates: [{ ...template, defaults: { ...template.defaults, cpu_count: 99 } }], host },
    { templates: [{ ...template, limits: { ...template.limits, disk_gib: { min: 100, max: 20 } } }], host },
  ])('rejects catalogue claims unsupported by this checkpoint: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getEnvironmentCatalog()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects invalid offsets before network access', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(getEnvironments('bee-1', undefined, -1)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(getEnvironments('bee-1', undefined, 1_000_001)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('computer planning state', () => {
  it('reads all pages without mutation', async () => {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.endsWith('/catalog') ? { templates: [template], host } : url.includes('offset=1') ? page([{ ...environment, id: 'environment-2', status: 'cancelled' }]) : page([environment], true, 1))))
    vi.stubGlobal('fetch', fetcher)
    expect((await loadEnvironments('bee-1')).environments).toHaveLength(2)
    expect(fetcher.mock.calls.every((call) => call[1].method === 'GET')).toBe(true)
  })
  it('rejects a repeated request across pages rather than presenting an inconsistent status', async () => {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.endsWith('/catalog') ? { templates: [template], host } : url.includes('offset=1') ? page([environment]) : page([environment], true, 1))))
    vi.stubGlobal('fetch', fetcher)
    await expect(loadEnvironments('bee-1')).rejects.toMatchObject({ code: 'state_conflict' })
  })
  it('keeps request identity stable for a lost response, and changes it only for a different plan', () => {
    const create = vi.fn().mockReturnValueOnce('uuid-1').mockReturnValueOnce('uuid-2'); const identity = planIdentity(create)
    expect(identity(plan)).toBe('uuid-1'); expect(identity({ ...plan })).toBe('uuid-1')
    expect(identity({ ...plan, disk_gib: 40 })).toBe('uuid-2'); expect(create).toHaveBeenCalledTimes(2)
  })
  it('keeps resource suggestions within server bounds', () => {
    expect(resourceProfile(template, false)).toEqual(template.defaults)
    expect(resourceProfile(template, true)).toEqual({ cpu_count: 4, memory_mib: 8192, disk_gib: 60 })
    expect(resourceProfile({ ...template, limits: { ...template.limits, cpu_count: { min: 1, max: 2 } } }, true).cpu_count).toBe(2)
  })
  it.each([
    { ...plan, name: '' }, { ...plan, name: ' '.repeat(3) }, { ...plan, name: 'x'.repeat(101) }, { ...plan, name: 'name\n' },
    { ...plan, cpu_count: NaN }, { ...plan, memory_mib: 1000 }, { ...plan, disk_gib: 101 }, { ...plan, cpu_count: 1.5 }, { ...plan, template_id: 'other' },
  ])('prevents invalid plan submission: %j', (value) => { expect(validPlan(value, template)).toBe(false) })
  it('recognizes uncertain responses without treating them as approval to repeat', () => {
    expect(uncertainEnvironmentMutation(new ApiError('mutation_network'))).toBe(true)
    expect(uncertainEnvironmentMutation(new ApiError('invalid_response'))).toBe(true)
    expect(uncertainEnvironmentMutation(new ApiError('http_500', 500))).toBe(true)
    expect(uncertainEnvironmentMutation(new ApiError('environment_conflict', 409))).toBe(false)
  })
})
