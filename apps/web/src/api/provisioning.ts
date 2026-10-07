import { ApiError, record, request } from './client'
import type { EnvironmentRecord } from './environments'

export interface ProvisioningHost { host_id: string; revision: number; fingerprint: string }
export interface ProvisioningSnapshot {
  format: 1; plan_id: string; installation_id: string; agent_id: string; host_id: string; host_revision: number
  environment_id: string; environment_revision: number; job_id: string; template_id: 'linux-desktop-v1'
  driver: 'hyperv'; mode: 'create_stopped_hardware'; vm_name: string; cpu_count: number; memory_bytes: number; disk_bytes: number
  image_iso_sha256: string; image_iso_size: number; payload_sha256: string; payload_size: number; catalog_hash: string
  network: 'none'; storage_profile: 'bees-managed-v1'; boot: false
}
export interface ProvisioningPlan {
  plan_id: string; plan_hash: string; revision: number; status: 'prepared' | 'authorized' | 'revoked' | 'dispatch_started' | 'hardware_verified' | 'outcome_unknown'
  created_at: string; authorization_expires_at: string | null; usable: false; context_valid: boolean
  reason_code: 'host_changed' | 'environment_changed' | 'catalog_changed' | 'installation_changed' | 'agent_inactive' | 'authorization_expired' | null
  plan: ProvisioningSnapshot
}
export interface ProvisioningReviewState { plan: ProvisioningPlan | null; host: ProvisioningHost | null; preparation_available: boolean }
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value) && value !== '00000000-0000-0000-0000-000000000000'
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const date = (value: unknown): value is string => typeof value === 'string' && !Number.isNaN(Date.parse(value))
function isSnapshot(value: unknown): value is ProvisioningSnapshot {
  return record(value) && value.format === 1 && ['plan_id', 'installation_id', 'agent_id', 'host_id', 'environment_id', 'job_id'].every((key) => uuid(value[key]))
    && positive(value.host_revision) && positive(value.environment_revision) && value.template_id === 'linux-desktop-v1' && value.driver === 'hyperv'
    && value.mode === 'create_stopped_hardware' && value.vm_name === `Bees-${value.installation_id}-${value.environment_id}`
    && positive(value.cpu_count) && value.cpu_count <= 4 && positive(value.memory_bytes) && value.memory_bytes >= 2048 * 1024 ** 2 && value.memory_bytes <= 8192 * 1024 ** 2
    && positive(value.disk_bytes) && value.disk_bytes >= 20 * 1024 ** 3 && value.disk_bytes <= 100 * 1024 ** 3
    && ['image_iso_sha256', 'payload_sha256', 'catalog_hash'].every((key) => hash(value[key]))
    && positive(value.image_iso_size) && value.image_iso_size <= 8 * 1024 ** 3 && positive(value.payload_size) && value.payload_size <= 8 * 1024 ** 3
    && value.network === 'none' && value.storage_profile === 'bees-managed-v1' && value.boot === false
}
export function isProvisioningPlan(value: unknown): value is ProvisioningPlan {
  return record(value) && uuid(value.plan_id) && hash(value.plan_hash) && positive(value.revision)
    && ['prepared', 'authorized', 'revoked', 'dispatch_started', 'hardware_verified', 'outcome_unknown'].includes(value.status as string)
    && date(value.created_at) && (value.authorization_expires_at === null || date(value.authorization_expires_at))
    && value.usable === false && typeof value.context_valid === 'boolean'
    && [null, 'host_changed', 'environment_changed', 'catalog_changed', 'installation_changed', 'agent_inactive', 'authorization_expired'].includes(value.reason_code as null | string)
    && isSnapshot(value.plan) && value.plan.plan_id === value.plan_id && (value.status !== 'authorized' || value.authorization_expires_at !== null)
}
function isHost(value: unknown): value is ProvisioningHost {
  return record(value) && uuid(value.host_id) && positive(value.revision) && typeof value.fingerprint === 'string' && /^[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$/.test(value.fingerprint)
}
function publicPlan(value: ProvisioningPlan): ProvisioningPlan {
  const source = value.plan
  const snapshot: ProvisioningSnapshot = { format: 1, plan_id: source.plan_id, installation_id: source.installation_id, agent_id: source.agent_id,
    host_id: source.host_id, host_revision: source.host_revision, environment_id: source.environment_id, environment_revision: source.environment_revision, job_id: source.job_id,
    template_id: 'linux-desktop-v1', driver: 'hyperv', mode: 'create_stopped_hardware', vm_name: source.vm_name, cpu_count: source.cpu_count, memory_bytes: source.memory_bytes,
    disk_bytes: source.disk_bytes, image_iso_sha256: source.image_iso_sha256, image_iso_size: source.image_iso_size, payload_sha256: source.payload_sha256, payload_size: source.payload_size,
    catalog_hash: source.catalog_hash, network: 'none', storage_profile: 'bees-managed-v1', boot: false }
  return { plan_id: value.plan_id, plan_hash: value.plan_hash, revision: value.revision, status: value.status, created_at: value.created_at,
    authorization_expires_at: value.authorization_expires_at, usable: false, context_valid: value.context_valid, reason_code: value.reason_code, plan: snapshot }
}
const path = (agent: string, environment: string) => `/agents/${encodeURIComponent(agent)}/environments/${encodeURIComponent(environment)}/provisioning`
function checkedPlan(value: unknown, agent: string, environment: string) {
  if (!isProvisioningPlan(value) || value.plan.agent_id !== agent || value.plan.environment_id !== environment) throw new ApiError('invalid_response')
  return publicPlan(value)
}
export async function getProvisioning(agent: string, environment: string, signal?: AbortSignal): Promise<ProvisioningReviewState> {
  const value = await request(path(agent, environment), { authenticated: true, signal })
  if (!record(value) || typeof value.preparation_available !== 'boolean' || !(value.host === null || isHost(value.host)) || value.preparation_available && value.host === null) throw new ApiError('invalid_response')
  const host = value.host === null ? null : { host_id: value.host.host_id, revision: value.host.revision, fingerprint: value.host.fingerprint }
  return { plan: value.plan === null ? null : checkedPlan(value.plan, agent, environment), host, preparation_available: value.preparation_available }
}
export async function prepareProvisioning(agent: string, environment: EnvironmentRecord, host: ProvisioningHost, requestId: string, signal?: AbortSignal): Promise<ProvisioningPlan> {
  const value = checkedPlan(await request(`${path(agent, environment.id)}/prepare`, { authenticated: true, signal,
    body: { host_id: host.host_id, expected_environment_revision: environment.revision, expected_host_revision: host.revision, client_request_id: requestId } }), agent, environment.id)
  if (value.plan.host_id !== host.host_id || value.plan.host_revision !== host.revision || value.plan.environment_revision !== environment.revision
    || value.plan.cpu_count !== environment.cpu_count || value.plan.memory_bytes !== environment.memory_mib * 1024 ** 2 || value.plan.disk_bytes !== environment.disk_gib * 1024 ** 3) throw new ApiError('invalid_response')
  return value
}
async function decide(agent: string, environment: string, plan: ProvisioningPlan, action: 'authorize' | 'revoke', requestId: string, signal?: AbortSignal) {
  const value = checkedPlan(await request(`${path(agent, environment)}/${encodeURIComponent(plan.plan_id)}/${action}`, { authenticated: true, signal,
    body: { expected_revision: plan.revision, plan_hash: plan.plan_hash, client_request_id: requestId } }), agent, environment)
  if (value.plan_id !== plan.plan_id || value.plan_hash !== plan.plan_hash || value.revision < plan.revision || JSON.stringify(value.plan) !== JSON.stringify(plan.plan)) throw new ApiError('invalid_response')
  // A replay can return a later state (revoked, started or unknown); it is never a fresh grant.
  return value
}
export const authorizeProvisioning = (agent: string, environment: string, plan: ProvisioningPlan, requestId: string, signal?: AbortSignal) => decide(agent, environment, plan, 'authorize', requestId, signal)
export const revokeProvisioning = (agent: string, environment: string, plan: ProvisioningPlan, requestId: string, signal?: AbortSignal) => decide(agent, environment, plan, 'revoke', requestId, signal)
