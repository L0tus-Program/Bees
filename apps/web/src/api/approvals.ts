import { ApiError, record, request } from './client'

export type ApprovalDecision = 'allow_once' | 'allow_rule' | 'ask' | 'deny'
export interface ApprovalRecord {
  id: string; agent_id: string; task_id: string; task_title: string; conversation_id: string
  task_objective?: string; expected_result?: string
  status: 'pending' | 'approved' | 'denied' | 'revoked'; decision: ApprovalDecision | null; revision: number
  created_at: string; decided_at: string | null; expires_at: string; consumed_at: string | null
  valid: boolean; stale_reason: string | null; tool_name: string; action: string; environment_id: string
  resource: string; identity: string; parameters: { model: string }; scope: Record<string, unknown>; consequence: string
}
export interface ApprovalCollection { approvals: ApprovalRecord[]; has_more: boolean; next_offset: number | null }
export interface DecisionInput { client_request_id: string; expected_revision: number; decision: ApprovalDecision; reason?: string }
const date = (value: unknown) => typeof value === 'string' && !Number.isNaN(Date.parse(value))
const optionalDate = (value: unknown) => value === null || date(value)
export function isApproval(value: unknown): value is ApprovalRecord {
  if (!record(value) || !record(value.parameters) || !record(value.scope)) return false
  return ['id', 'agent_id', 'task_id', 'task_title', 'conversation_id', 'tool_name', 'action', 'environment_id', 'resource', 'identity', 'consequence'].every((key) => typeof value[key] === 'string')
    && ['pending', 'approved', 'denied', 'revoked'].includes(String(value.status))
    && (value.decision === null || ['allow_once', 'allow_rule', 'ask', 'deny'].includes(String(value.decision)))
    && Number.isInteger(value.revision) && (value.revision as number) > 0
    && date(value.created_at) && date(value.expires_at) && optionalDate(value.decided_at) && optionalDate(value.consumed_at)
    && typeof value.valid === 'boolean' && (value.stale_reason === null || typeof value.stale_reason === 'string')
    && (value.task_objective === undefined || typeof value.task_objective === 'string') && (value.expected_result === undefined || typeof value.expected_result === 'string')
    && typeof value.parameters.model === 'string' && Object.keys(value.parameters).every((key) => key === 'model')
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/approvals`
function checked(value: unknown, agentId: string, approvalId?: string, taskId?: string): ApprovalRecord {
  if (!isApproval(value) || value.agent_id !== agentId || (approvalId !== undefined && value.id !== approvalId) || (taskId !== undefined && value.task_id !== taskId)) throw new ApiError('invalid_response')
  return value
}
export async function getApprovals(agentId: string, signal?: AbortSignal, offset = 0, taskId?: string): Promise<ApprovalCollection> {
  if (!Number.isInteger(offset) || offset < 0) throw new ApiError('invalid_request')
  const query = `offset=${offset}&limit=100${taskId === undefined ? '' : `&task_id=${encodeURIComponent(taskId)}`}`
  const value = await request(`${base(agentId)}?${query}`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.approvals) || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  const approvals = value.approvals.map((approval) => checked(approval, agentId, undefined, taskId))
  if (new Set(approvals.map((approval) => approval.id)).size !== approvals.length || approvals.length > 100
    || (value.has_more ? approvals.length === 0 || value.next_offset !== offset + approvals.length : value.next_offset !== null)) throw new ApiError('invalid_response')
  return { approvals, has_more: value.has_more, next_offset: value.next_offset as number | null }
}
export async function decideApproval(agentId: string, approvalId: string, body: DecisionInput, signal?: AbortSignal) {
  const approval = checked(await request(`${base(agentId)}/${encodeURIComponent(approvalId)}/decision`, { body, authenticated: true, signal }), agentId, approvalId)
  const status = body.decision === 'ask' ? 'pending' : body.decision === 'deny' ? 'denied' : 'approved'
  if (approval.decision !== body.decision || approval.status !== status || approval.revision <= body.expected_revision) throw new ApiError('invalid_response')
  return approval
}
export async function revokeApproval(agentId: string, approvalId: string, expectedRevision: number, signal?: AbortSignal) {
  const approval = checked(await request(`${base(agentId)}/${encodeURIComponent(approvalId)}`, { body: { expected_revision: expectedRevision, status: 'revoked' }, method: 'PATCH', authenticated: true, signal }), agentId, approvalId)
  if (approval.status !== 'revoked' || approval.revision <= expectedRevision) throw new ApiError('invalid_response')
  return approval
}
