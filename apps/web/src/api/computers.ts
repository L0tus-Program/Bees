import { ApiError, record, request } from './client'

export interface Computer {
  id: string; agent_id: string; executor_id: string; name: string
  status: 'provisioning' | 'ready' | 'failed' | 'removing' | 'removed'
  revision: number; control_holder: 'agent' | 'human'; control_revision: number; reason_code: string | null
  online: boolean; created_at: string; updated_at: string
}
export interface ComputerState { computers: Computer[]; runtime_online: boolean; runtime_paired: boolean; local_link_available: boolean }
export interface Executor {
  id: string; kind: 'computer_runtime' | 'personal'; name: string; status: 'active' | 'revoked'; revision: number
  online: boolean; last_seen_at: string | null; version: string | null; platform: string | null; capabilities: string[]; roots?: string[]; created_at: string
}
export interface ExecutorGrant { id: string; agent_id: string; enabled: boolean; revision: number; scope: { roots: string[]; write: boolean; shell: boolean; browser: boolean } }
export interface Invite { invite_id: string; token: string; expires_at: string; delivered_locally: boolean }

const text = (value: unknown): value is string => typeof value === 'string'
const agentPath = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/computers`

function isComputer(value: unknown): value is Computer {
  return record(value) && ['id', 'agent_id', 'executor_id', 'name', 'status', 'control_holder'].every((key) => text(value[key]))
    && Number.isSafeInteger(value.revision) && Number.isSafeInteger(value.control_revision) && typeof value.online === 'boolean'
}
function isExecutor(value: unknown): value is Executor {
  return record(value) && ['id', 'kind', 'name', 'status'].every((key) => text(value[key])) && typeof value.online === 'boolean'
    && Number.isSafeInteger(value.revision) && Array.isArray(value.capabilities)
}

export async function getComputers(agentId: string, signal?: AbortSignal): Promise<ComputerState> {
  const value = await request(agentPath(agentId), { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.computers) || !value.computers.every(isComputer) || typeof value.runtime_online !== 'boolean') throw new ApiError('invalid_response')
  return value as unknown as ComputerState
}
export async function createComputer(agentId: string, clientRequestId: string, name?: string) {
  const value = await request(agentPath(agentId), { authenticated: true, body: { client_request_id: clientRequestId, ...(name ? { name } : {}) }, timeoutMs: 20000 })
  if (!isComputer(value)) throw new ApiError('invalid_response')
  return value
}
export async function removeComputer(computer: Computer) {
  const value = await request(`${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/remove`, { authenticated: true, body: { expected_revision: computer.revision } })
  if (!isComputer(value)) throw new ApiError('invalid_response')
  return value
}
export async function setComputerControl(computer: Computer, holder: 'agent' | 'human') {
  const value = await request(`${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/control`, { authenticated: true, body: { expected_revision: computer.control_revision, holder } })
  if (!isComputer(value)) throw new ApiError('invalid_response')
  return value
}
export function viewUrl(computer: Computer) {
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${scheme}//${window.location.host}/api/v1${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/view`
}
export async function getExecutors(kind?: Executor['kind'], signal?: AbortSignal): Promise<Executor[]> {
  const value = await request(`/executors${kind ? `?kind=${kind}` : ''}`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.executors) || !value.executors.every(isExecutor)) throw new ApiError('invalid_response')
  return value.executors
}
export async function createInvite(kind: Executor['kind'], name: string): Promise<Invite> {
  const value = await request('/executors/invites', { authenticated: true, body: { kind, name } })
  if (!record(value) || !text(value.token) || !text(value.expires_at)) throw new ApiError('invalid_response')
  return value as unknown as Invite
}
export async function revokeExecutor(executor: Executor) {
  const value = await request(`/executors/${encodeURIComponent(executor.id)}/revoke`, { authenticated: true, body: { expected_revision: executor.revision } })
  if (!isExecutor(value)) throw new ApiError('invalid_response')
  return value
}
export async function getGrants(executorId: string, signal?: AbortSignal): Promise<ExecutorGrant[]> {
  const value = await request(`/executors/${encodeURIComponent(executorId)}/grants`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.grants)) throw new ApiError('invalid_response')
  return value.grants as ExecutorGrant[]
}
export async function setGrant(executorId: string, agentId: string, body: { expected_revision: number; enabled: boolean; roots: string[]; write: boolean; shell: boolean; browser: boolean }) {
  const value = await request(`/executors/${encodeURIComponent(executorId)}/grants/${encodeURIComponent(agentId)}`, { authenticated: true, method: 'PUT', body })
  if (!record(value) || !text(value.id)) throw new ApiError('invalid_response')
  return value as unknown as ExecutorGrant
}
