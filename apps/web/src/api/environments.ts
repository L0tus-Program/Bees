import { ApiError, record, request } from './client'

export interface Resources { cpu_count: number; memory_mib: number; disk_gib: number }
export interface EnvironmentTemplate {
  id: string; name: string; version: number; os: 'linux'; applications: string[]; status: 'planned'; provisionable: false
  defaults: Resources; limits: { [K in keyof Resources]: { min: number; max: number } }
}
export interface EnvironmentHost {
  driver: 'none' | 'hyperv' | 'libvirt'; status: 'host_setup_required' | 'guest_bridge_required' | 'driver_unavailable'; provisionable: false
  probe: Record<string, unknown> | null
}
export interface EnvironmentRecord extends Resources {
  id: string; agent_id: string; name: string; template_id: string; status: 'awaiting_host' | 'provisioning' | 'cancelled' | 'outcome_unknown'
  revision: number; created_at: string; updated_at: string; reason_code: string
  usable: false
}
export interface EnvironmentCatalog { templates: EnvironmentTemplate[]; host: EnvironmentHost }
export interface EnvironmentPlan extends Resources { name: string; template_id: string }
const resourceKeys = ['cpu_count', 'memory_mib', 'disk_gib'] as const
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0
const positive = (v: unknown): v is number => Number.isInteger(v) && (v as number) > 0
const date = (v: unknown) => typeof v === 'string' && !Number.isNaN(Date.parse(v))
const resources = (v: unknown): v is Resources => record(v) && resourceKeys.every((key) => positive(v[key]))
export function isEnvironmentHost(v: unknown): v is EnvironmentHost {
  return record(v) && ['none', 'hyperv', 'libvirt'].includes(v.driver as string)
    && ['host_setup_required', 'guest_bridge_required', 'driver_unavailable'].includes(v.status as string)
    && v.provisionable === false && (v.probe === null || record(v.probe))
}
export function isEnvironmentTemplate(v: unknown): v is EnvironmentTemplate {
  if (!record(v) || !text(v.id) || !text(v.name) || !positive(v.version) || v.os !== 'linux'
    || v.status !== 'planned' || v.provisionable !== false || !resources(v.defaults) || !record(v.limits)
    || !Array.isArray(v.applications) || !v.applications.every(text) || new Set(v.applications).size !== v.applications.length) return false
  const limits = v.limits; const defaults = v.defaults
  return resourceKeys.every((key) => {
    const range = limits[key]
    return record(range) && positive(range.min) && positive(range.max) && range.min <= range.max && defaults[key] >= range.min && defaults[key] <= range.max
  })
}
export function isEnvironment(v: unknown): v is EnvironmentRecord {
  return record(v) && ['id', 'agent_id', 'name', 'template_id', 'reason_code'].every((key) => text(v[key]))
    && ['awaiting_host', 'provisioning', 'cancelled', 'outcome_unknown'].includes(v.status as string) && positive(v.revision) && resources(v)
    && date(v.created_at) && date(v.updated_at) && v.usable === false
}
const path = (agent: string) => `/agents/${encodeURIComponent(agent)}/environments`
export async function getEnvironmentCatalog(signal?: AbortSignal): Promise<EnvironmentCatalog> {
  const v = await request('/environments/catalog', { authenticated: true, signal })
  if (!record(v) || !isEnvironmentHost(v.host) || !Array.isArray(v.templates) || !v.templates.every(isEnvironmentTemplate)
    || new Set(v.templates.map((entry) => entry.id)).size !== v.templates.length) throw new ApiError('invalid_response')
  return { templates: v.templates, host: v.host }
}
export async function getEnvironments(agent: string, signal?: AbortSignal, offset = 0) {
  if (!Number.isInteger(offset) || offset < 0 || offset > 1_000_000) throw new ApiError('invalid_request')
  const v = await request(`${path(agent)}?offset=${offset}&limit=100`, { authenticated: true, signal })
  if (!record(v) || !Array.isArray(v.environments) || !v.environments.every(isEnvironment) || typeof v.has_more !== 'boolean'
    || v.environments.length > 100 || v.environments.some((entry) => entry.agent_id !== agent)
    || new Set(v.environments.map((entry) => entry.id)).size !== v.environments.length
    || (v.has_more ? v.environments.length === 0 || v.next_offset !== offset + v.environments.length : v.next_offset !== null)) throw new ApiError('invalid_response')
  return { items: v.environments, has_more: v.has_more, next_offset: v.next_offset as number | null }
}
export async function createEnvironment(agent: string, plan: EnvironmentPlan, requestId: string, signal?: AbortSignal): Promise<EnvironmentRecord> {
  const v = await request(path(agent), { authenticated: true, signal, body: { ...plan, client_request_id: requestId } })
  if (!isEnvironment(v) || v.agent_id !== agent || v.name !== plan.name || v.template_id !== plan.template_id
    || resourceKeys.some((key) => v[key] !== plan[key])) throw new ApiError('invalid_response')
  // Replay may return a request later cancelled in another session.
  return v
}
export async function cancelEnvironment(agent: string, environment: EnvironmentRecord, requestId: string, signal?: AbortSignal): Promise<EnvironmentRecord> {
  if (environment.status !== 'awaiting_host') throw new ApiError('environment_conflict', 409)
  const v = await request(`${path(agent)}/${encodeURIComponent(environment.id)}/cancel`, { authenticated: true, signal, body: { expected_revision: environment.revision, client_request_id: requestId } })
  if (!isEnvironment(v) || v.agent_id !== agent || v.id !== environment.id || v.status !== 'cancelled'
    || v.name !== environment.name || v.template_id !== environment.template_id || v.revision < environment.revision
    || resourceKeys.some((key) => v[key] !== environment[key])) throw new ApiError('invalid_response')
  return v
}

export interface HostDiagnostic {
  driver: 'hyperv' | 'libvirt'; status: 'driver_unavailable' | 'guest_bridge_required'; probe: Record<string, boolean>; provisionable: false
}
export interface PairedHost {
  host_id: string; installation_id: string; status: 'pending' | 'active' | 'revoked'; revision: number; report_revision: number
  fingerprint: string; created_at: string; paired_at: string | null; last_seen: string | null; pairing_expires_at: string
  diagnostic: HostDiagnostic | null; online: boolean; confirmable: boolean; provisionable: false
}
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)
function isHostDiagnostic(v: unknown): v is HostDiagnostic {
  if (!record(v) || !['hyperv', 'libvirt'].includes(v.driver as string) || !['driver_unavailable', 'guest_bridge_required'].includes(v.status as string) || v.provisionable !== false || !record(v.probe)) return false
  const keys = v.driver === 'hyperv' ? ['platform', 'module', 'service'] : ['platform', 'kvm', 'service']
  return Object.keys(v.probe).length === keys.length && keys.every((key) => typeof (v.probe as Record<string, unknown>)[key] === 'boolean')
}
export function isPairedHost(v: unknown): v is PairedHost {
  return record(v) && uuid(v.host_id) && uuid(v.installation_id) && ['pending', 'active', 'revoked'].includes(v.status as string)
    && positive(v.revision) && Number.isInteger(v.report_revision) && (v.report_revision as number) >= 0
    && typeof v.fingerprint === 'string' && /^[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$/.test(v.fingerprint)
    && date(v.created_at) && date(v.pairing_expires_at) && (v.paired_at === null || date(v.paired_at)) && (v.last_seen === null || date(v.last_seen))
    && (v.diagnostic === null || isHostDiagnostic(v.diagnostic)) && typeof v.online === 'boolean' && typeof v.confirmable === 'boolean' && v.provisionable === false
    && (v.diagnostic === null || v.last_seen !== null && positive(v.report_revision)) && (v.status !== 'active' || v.paired_at !== null)
    && (!v.online || v.status === 'active' && v.last_seen !== null && v.diagnostic !== null)
    && (!v.confirmable || v.status === 'pending') && (v.status !== 'pending' || v.diagnostic === null && v.last_seen === null && v.paired_at === null)
}
function hostView(host: PairedHost): PairedHost {
  // Keep only the public diagnostic projection; transport additions never become form state.
  return { host_id: host.host_id, installation_id: host.installation_id, status: host.status, revision: host.revision, report_revision: host.report_revision,
    fingerprint: host.fingerprint, created_at: host.created_at, paired_at: host.paired_at, last_seen: host.last_seen, pairing_expires_at: host.pairing_expires_at,
    diagnostic: host.diagnostic && { driver: host.diagnostic.driver, status: host.diagnostic.status, probe: { ...host.diagnostic.probe }, provisionable: false },
    online: host.online, confirmable: host.confirmable, provisionable: false }
}
export async function getPairedHosts(signal?: AbortSignal, offset = 0) {
  if (!Number.isInteger(offset) || offset < 0 || offset > 1_000_000) throw new ApiError('invalid_request')
  const v = await request(`/environments/hosts?offset=${offset}&limit=100`, { authenticated: true, signal })
  if (!record(v) || !Array.isArray(v.hosts) || !v.hosts.every(isPairedHost) || typeof v.has_more !== 'boolean'
    || v.hosts.length > 100 || new Set(v.hosts.map((host) => host.host_id)).size !== v.hosts.length
    || new Set(v.hosts.map((host) => host.installation_id)).size > 1
    || (v.has_more ? v.hosts.length === 0 || v.next_offset !== offset + v.hosts.length : v.next_offset !== null)) throw new ApiError('invalid_response')
  return { items: v.hosts.map(hostView), has_more: v.has_more, next_offset: v.next_offset as number | null }
}
async function changeHost(host: PairedHost, operation: 'confirm' | 'revoke', requestId: string, signal?: AbortSignal) {
  const v = await request(`/environments/hosts/${encodeURIComponent(host.host_id)}/${operation}`, { authenticated: true, signal,
    body: { expected_revision: host.revision, client_request_id: requestId, ...(operation === 'confirm' ? { fingerprint: host.fingerprint } : {}) } })
  if (!isPairedHost(v) || v.host_id !== host.host_id || v.installation_id !== host.installation_id || v.fingerprint !== host.fingerprint || v.revision < host.revision
    || v.created_at !== host.created_at || v.pairing_expires_at !== host.pairing_expires_at || v.status !== (operation === 'confirm' ? 'active' : 'revoked')) throw new ApiError('invalid_response')
  return hostView(v)
}
export const confirmHostPair = (host: PairedHost, requestId: string, signal?: AbortSignal) => changeHost(host, 'confirm', requestId, signal)
export const revokeHostPair = (host: PairedHost, requestId: string, signal?: AbortSignal) => changeHost(host, 'revoke', requestId, signal)
