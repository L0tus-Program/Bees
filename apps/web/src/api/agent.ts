import { ApiError, record, request } from './client'
import { digest } from './toolTasks'

export interface AgentTool { tool_name: string; function: string; target: string; effect: string; default_policy: string; plugin_id: string }
export interface AgentCapabilities {
  tool_calls_supported: boolean; tools: AgentTool[]; available: boolean; recommended_installed: boolean
  worker: { available: boolean; last_seen: string | null }; defaults: { max_calls: number; max_active_seconds: number }
}
export interface AgentApproval {
  id: string; agent_id: string; task_id: string; run_id: string; status: 'pending' | 'approved' | 'denied' | 'revoked'; decision: string | null
  revision: number; created_at: string; expires_at: string; consumed_at: string | null; valid: boolean; stale_reason: string | null
  tool_name: string; effect: string; summary: string; task_title: string; target: { kind: string; environment_id: string }
  arguments_preview: Record<string, unknown> | null
}
export type AgentDecision = 'allow_once' | 'allow_tool' | 'deny'

const text = (value: unknown): value is string => typeof value === 'string'
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}`

function isTool(value: unknown): value is AgentTool {
  return record(value) && ['tool_name', 'function', 'target', 'effect', 'default_policy', 'plugin_id'].every((key) => text(value[key]))
}

export async function getAgentCapabilities(agentId: string, signal?: AbortSignal): Promise<AgentCapabilities> {
  const value = await request(`${base(agentId)}/agent/capabilities`, { authenticated: true, signal })
  if (!record(value) || typeof value.available !== 'boolean' || typeof value.tool_calls_supported !== 'boolean'
    || typeof value.recommended_installed !== 'boolean' || !Array.isArray(value.tools) || !value.tools.every(isTool)
    || !record(value.worker) || !record(value.defaults)) throw new ApiError('invalid_response')
  return value as unknown as AgentCapabilities
}

export async function enableRecommendedTools(agentId: string, clientRequestId: string, signal?: AbortSignal) {
  const value = await request(`${base(agentId)}/agent/recommended-tools`, { authenticated: true, signal, body: { client_request_id: clientRequestId } })
  if (!record(value) || !Array.isArray(value.granted)) throw new ApiError('invalid_response')
  return value.granted as string[]
}

export interface AgentAttachments { attachments: { artifact_id: string; revision: number; sha256: string }[]; attachment_consent: { expected_agent_revision: number } }
export async function sendAgentMessage(agentId: string, conversationId: string, content: string, clientRequestId: string, signal?: AbortSignal, mode: 'agent' | 'research' = 'agent', files?: AgentAttachments) {
  const value = await request(`${base(agentId)}/conversations/${encodeURIComponent(conversationId)}/agent-messages`, {
    authenticated: true, signal, body: { client_request_id: clientRequestId, content, ...(mode === 'research' ? { mode } : {}), ...(files?.attachments.length ? files : {}) }, timeoutMs: 20000,
  })
  if (!record(value) || !text(value.task_id)) throw new ApiError('invalid_response')
  return { task_id: value.task_id, message_id: value.message_id as string | null, status: value.status as string }
}

export async function createAgentTask(agentId: string, body: { client_request_id: string; title: string; objective: string; expected_result?: string; max_calls?: number; max_active_seconds?: number }, signal?: AbortSignal) {
  const value = await request(`${base(agentId)}/agent-tasks`, { authenticated: true, signal, body })
  if (!record(value) || !text(value.task_id)) throw new ApiError('invalid_response')
  return value.task_id
}

function isApproval(value: unknown): value is AgentApproval {
  return record(value) && ['id', 'agent_id', 'task_id', 'run_id', 'tool_name', 'summary', 'task_title', 'effect'].every((key) => text(value[key]))
    && ['pending', 'approved', 'denied', 'revoked'].some((status) => value.status === status)
    && Number.isSafeInteger(value.revision) && (value.revision as number) > 0 && typeof value.valid === 'boolean'
    && ['created_at', 'expires_at'].every((key) => text(value[key]) && Number.isFinite(Date.parse(value[key])))
    && (value.consumed_at === null || text(value.consumed_at) && Number.isFinite(Date.parse(value.consumed_at)))
    && (value.decision === null || text(value.decision)) && (value.stale_reason === null || text(value.stale_reason))
    && record(value.target) && text(value.target.kind) && text(value.target.environment_id)
    && (value.arguments_preview === null || record(value.arguments_preview))
}

export async function listAgentApprovals(agentId: string, options: { taskId?: string; status?: string } = {}, signal?: AbortSignal): Promise<AgentApproval[]> {
  const query = new URLSearchParams({ limit: '50' })
  if (options.taskId) query.set('task_id', options.taskId)
  if (options.status) query.set('status', options.status)
  const value = await request(`${base(agentId)}/agent-tool-approvals?${query.toString()}`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.approvals) || !value.approvals.every((item): item is AgentApproval =>
    isApproval(item) && item.agent_id === agentId && (!options.taskId || item.task_id === options.taskId)
    && (!options.status || item.status === options.status))) throw new ApiError('invalid_response')
  if (new Set(value.approvals.map((item) => item.id)).size !== value.approvals.length) throw new ApiError('invalid_response')
  return value.approvals
}

export async function getAgentApproval(agentId: string, approval: AgentApproval, signal?: AbortSignal): Promise<AgentApproval> {
  const value = await request(`${base(agentId)}/agent-tool-approvals/${encodeURIComponent(approval.id)}`, { authenticated: true, signal })
  if (!isApproval(value) || value.agent_id !== agentId || value.id !== approval.id
    || value.task_id !== approval.task_id || value.run_id !== approval.run_id) throw new ApiError('invalid_response')
  return value
}

async function mutateApproval(agentId: string, approval: AgentApproval, clientRequestId: string, decision: AgentDecision | 'revoke', signal?: AbortSignal) {
  const operation = decision === 'revoke' ? 'revoke' : 'decide'
  const body = {
    client_request_id: clientRequestId, expected_revision: approval.revision,
    ...(decision === 'revoke' ? { status: 'revoked' } : { decision, reason: '', rule_name: null }),
  }
  const requestHash = await digest({ operation, input: body })
  const path = `${base(agentId)}/agent-tool-approvals/${encodeURIComponent(approval.id)}`
  const value = await request(operation === 'revoke' ? path : `${path}/decision`, {
    authenticated: true, signal, method: operation === 'revoke' ? 'PATCH' : undefined, body,
  })
  if (!record(value) || !record(value.receipt) || value.receipt.confirmed !== true
    || value.receipt.approval_id !== approval.id || value.receipt.client_request_id !== clientRequestId
    || value.receipt.operation !== operation || value.receipt.expected_revision !== approval.revision
    || value.receipt.request_hash !== requestHash) throw new ApiError('invalid_response')

  const current = value.approval
  if (!isApproval(current) || current.agent_id !== agentId || current.id !== approval.id || current.task_id !== approval.task_id
    || current.run_id !== approval.run_id || current.revision <= approval.revision) throw new ApiError('invalid_response')
  // O recibo confirma o pedido original, enquanto a projeção pode já refletir uma revogação posterior.
  const expectedStatus = decision === 'revoke' ? 'revoked' : decision === 'deny' ? 'denied' : 'approved'
  const laterRevocation = expectedStatus === 'approved' && current.status === 'revoked'
  if ((current.status !== expectedStatus && !laterRevocation)
    || (operation === 'decide' && current.decision !== decision)) throw new ApiError('invalid_response')
  return current
}

export function revokeAgentApproval(agentId: string, approval: AgentApproval, clientRequestId: string, signal?: AbortSignal) {
  return mutateApproval(agentId, approval, clientRequestId, 'revoke', signal)
}

export function decideAgentApproval(agentId: string, approval: AgentApproval, decision: AgentDecision, clientRequestId: string, signal?: AbortSignal) {
  return mutateApproval(agentId, approval, clientRequestId, decision, signal)
}
