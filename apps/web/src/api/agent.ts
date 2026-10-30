import { ApiError, record, request } from './client'

export interface AgentTool { tool_name: string; function: string; target: string; effect: string; default_policy: string; plugin_id: string }
export interface AgentCapabilities {
  tool_calls_supported: boolean; tools: AgentTool[]; available: boolean; recommended_installed: boolean
  worker: { available: boolean; last_seen: string | null }; defaults: { max_calls: number; max_active_seconds: number }
}
export interface AgentApproval {
  id: string; task_id: string; run_id: string; status: 'pending' | 'approved' | 'denied' | 'revoked'; decision: string | null
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

export async function sendAgentMessage(agentId: string, conversationId: string, content: string, clientRequestId: string, signal?: AbortSignal, mode: 'agent' | 'research' = 'agent') {
  const value = await request(`${base(agentId)}/conversations/${encodeURIComponent(conversationId)}/agent-messages`, {
    authenticated: true, signal, body: { client_request_id: clientRequestId, content, ...(mode === 'research' ? { mode } : {}) }, timeoutMs: 20000,
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
  return record(value) && ['id', 'task_id', 'status', 'tool_name', 'summary', 'created_at'].every((key) => text(value[key]))
    && Number.isSafeInteger(value.revision) && typeof value.valid === 'boolean' && record(value.target)
}

export async function listAgentApprovals(agentId: string, options: { taskId?: string; status?: string } = {}, signal?: AbortSignal): Promise<AgentApproval[]> {
  const query = new URLSearchParams({ limit: '50' })
  if (options.taskId) query.set('task_id', options.taskId)
  if (options.status) query.set('status', options.status)
  const value = await request(`${base(agentId)}/agent-tool-approvals?${query.toString()}`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.approvals) || !value.approvals.every(isApproval)) throw new ApiError('invalid_response')
  return value.approvals
}

export async function getAgentApproval(agentId: string, approvalId: string, signal?: AbortSignal): Promise<AgentApproval> {
  const value = await request(`${base(agentId)}/agent-tool-approvals/${encodeURIComponent(approvalId)}`, { authenticated: true, signal })
  if (!isApproval(value)) throw new ApiError('invalid_response')
  return value
}

export async function revokeAgentApproval(agentId: string, approval: AgentApproval, clientRequestId: string, signal?: AbortSignal) {
  const value = await request(`${base(agentId)}/agent-tool-approvals/${encodeURIComponent(approval.id)}`, {
    authenticated: true, signal, method: 'PATCH', body: { client_request_id: clientRequestId, expected_revision: approval.revision },
  })
  if (!record(value) || !isApproval(value.approval)) throw new ApiError('invalid_response')
  return value.approval
}

export async function decideAgentApproval(agentId: string, approval: AgentApproval, decision: AgentDecision, clientRequestId: string, signal?: AbortSignal) {
  const value = await request(`${base(agentId)}/agent-tool-approvals/${encodeURIComponent(approval.id)}/decision`, {
    authenticated: true, signal, body: { client_request_id: clientRequestId, expected_revision: approval.revision, decision },
  })
  if (!record(value) || !isApproval(value.approval) || !record(value.receipt) || value.receipt.confirmed !== true) throw new ApiError('invalid_response')
  return value.approval
}
