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
const identifier = (value: unknown): value is string => text(value) && value.trim().length > 0
const revision = (value: unknown): boolean => Number.isSafeInteger(value) && (value as number) > 0
const agentPath = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/computers`

function isComputer(value: unknown): value is Computer {
  return record(value) && ['id', 'agent_id', 'executor_id'].every((key) => identifier(value[key]))
    && text(value.name) && text(value.status) && ['provisioning', 'ready', 'failed', 'removing', 'removed'].includes(value.status)
    && (value.control_holder === 'agent' || value.control_holder === 'human')
    && revision(value.revision) && revision(value.control_revision) && typeof value.online === 'boolean'
    && (value.reason_code === null || text(value.reason_code)) && text(value.created_at) && text(value.updated_at)
}
function isComputerReceipt(value: unknown, computer: Computer): value is Computer {
  return isComputer(value) && value.agent_id === computer.agent_id && value.id === computer.id && value.executor_id === computer.executor_id
}
function isExecutor(value: unknown): value is Executor {
  return record(value) && ['id', 'kind', 'name', 'status'].every((key) => text(value[key])) && typeof value.online === 'boolean'
    && Number.isSafeInteger(value.revision) && Array.isArray(value.capabilities)
}

export async function getComputers(agentId: string, signal?: AbortSignal): Promise<ComputerState> {
  const value = await request(agentPath(agentId), { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.computers)
    || !value.computers.every((item) => isComputer(item) && item.agent_id === agentId)
    || new Set(value.computers.map((item) => item.id)).size !== value.computers.length
    || typeof value.runtime_online !== 'boolean' || typeof value.runtime_paired !== 'boolean' || typeof value.local_link_available !== 'boolean') throw new ApiError('invalid_response')
  return value as unknown as ComputerState
}
export async function createComputer(agentId: string, clientRequestId: string, name?: string) {
  const value = await request(agentPath(agentId), { authenticated: true, body: { client_request_id: clientRequestId, ...(name ? { name } : {}) }, timeoutMs: 20000 })
  if (!isComputer(value) || value.agent_id !== agentId || value.id !== clientRequestId) throw new ApiError('invalid_response')
  return value
}
export async function removeComputer(computer: Computer) {
  const value = await request(`${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/remove`, { authenticated: true, body: { expected_revision: computer.revision } })
  if (!isComputerReceipt(value, computer)) throw new ApiError('invalid_response')
  return value
}
export async function setComputerControl(computer: Computer, holder: 'agent' | 'human') {
  const value = await request(`${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/control`, { authenticated: true, body: { expected_revision: computer.control_revision, holder } })
  if (!isComputerReceipt(value, computer)) throw new ApiError('invalid_response')
  return value
}
/** Fecha programas e sobe a área de trabalho de novo; a API aguarda o runtime confirmar. */
export async function restartComputer(computer: Computer, clientRequestId: string) {
  const value = await request(`${agentPath(computer.agent_id)}/${encodeURIComponent(computer.id)}/restart`, { authenticated: true, body: { client_request_id: clientRequestId }, timeoutMs: 100000 })
  if (!isComputerReceipt(value, computer) || !record(value) || value.restarted !== true) throw new ApiError('invalid_response')
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
