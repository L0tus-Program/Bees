import { ApiError, record, request } from './client'

export type PolicyEffect = 'allow' | 'ask' | 'deny'
export interface PolicyScope extends Record<string, unknown> {
  tool_name?: string | null; action?: string | null; environment_id?: string | null
  resource?: string | null; identity?: string | null; parameters?: Record<string, unknown>
}
export interface PolicyRecord {
  id: string; agent_id: string | null; name: string; effect: PolicyEffect; scope: Record<string, unknown>
  status: 'active' | 'revoked'; revision: number; reason: string; created_at: string; updated_at: string
}
export interface PolicyCollection { policies: PolicyRecord[]; has_more: boolean; next_offset: number | null }
export interface PolicyInput { name: string; effect: PolicyEffect; scope: PolicyScope; reason: string }
export interface PolicyUpdate extends Partial<PolicyInput> { expected_revision: number; status?: PolicyRecord['status'] }

const date = (value: unknown) => typeof value === 'string' && !Number.isNaN(Date.parse(value))
export function isPolicy(value: unknown): value is PolicyRecord {
  if (!record(value) || !record(value.scope)) return false
  return ['id', 'name', 'reason'].every((key) => typeof value[key] === 'string')
    && (value.agent_id === null || typeof value.agent_id === 'string')
    && ['allow', 'ask', 'deny'].includes(String(value.effect)) && ['active', 'revoked'].includes(String(value.status))
    && Number.isInteger(value.revision) && (value.revision as number) > 0 && date(value.created_at) && date(value.updated_at)
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/policies`
function checked(value: unknown, agentId: string, policyId?: string, allowGlobal = false): PolicyRecord {
  if (!isPolicy(value) || (value.agent_id !== agentId && !(allowGlobal && value.agent_id === null)) || (policyId !== undefined && value.id !== policyId)) throw new ApiError('invalid_response')
  return value
}
export async function getPolicies(agentId: string, signal?: AbortSignal, offset = 0): Promise<PolicyCollection> {
  if (!Number.isInteger(offset) || offset < 0) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}?offset=${offset}&limit=100`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.policies) || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  const policies = value.policies.map((policy) => checked(policy, agentId, undefined, true))
  if (new Set(policies.map((policy) => policy.id)).size !== policies.length) throw new ApiError('invalid_response')
  if (policies.length > 100 || (value.has_more ? policies.length === 0 || value.next_offset !== offset + policies.length : value.next_offset !== null)) throw new ApiError('invalid_response')
  return { policies, has_more: value.has_more, next_offset: value.next_offset as number | null }
}
export async function createPolicy(agentId: string, body: PolicyInput & { client_request_id: string }, signal?: AbortSignal) {
  return checked(await request(base(agentId), { body, authenticated: true, signal }), agentId)
}
export async function updatePolicy(agentId: string, policyId: string, body: PolicyUpdate, signal?: AbortSignal) {
  return checked(await request(`${base(agentId)}/${encodeURIComponent(policyId)}`, { body, method: 'PATCH', authenticated: true, signal }), agentId, policyId)
}
