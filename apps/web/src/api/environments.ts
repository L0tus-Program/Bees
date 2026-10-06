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
  id: string; agent_id: string; name: string; template_id: string; status: 'awaiting_host' | 'cancelled' | 'outcome_unknown'
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
    && ['awaiting_host', 'cancelled', 'outcome_unknown'].includes(v.status as string) && positive(v.revision) && resources(v)
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
  const v = await request(`${path(agent)}/${encodeURIComponent(environment.id)}/cancel`, { authenticated: true, signal, body: { expected_revision: environment.revision, client_request_id: requestId } })
  if (!isEnvironment(v) || v.agent_id !== agent || v.id !== environment.id || v.status !== 'cancelled'
    || v.name !== environment.name || v.template_id !== environment.template_id || v.revision < environment.revision
    || resourceKeys.some((key) => v[key] !== environment[key])) throw new ApiError('invalid_response')
  return v
}
