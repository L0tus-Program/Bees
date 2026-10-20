import { ApiError, record, request } from './client'
import { count, exact, timestamp, uuid } from './activity'
import { actionStatuses } from './tasks'
import type { ApprovalDecision } from './approvals'

export interface ResourceTarget { installation_id: string; environment_id: string; resource_id: string; adapter: 'local_process_text'; agent_id: string; identity: 'bees_user' }
export interface ResourceApproval {
  kind: 'resource_tool'; id: string; agent_id: string; task_id: string; run_id: string; task_title: string
  intent_action_id: string; effect_action_id: string; effect_status: typeof actionStatuses[number] | null
  revision: number; status: 'pending' | 'approved' | 'denied' | 'revoked'; decision: ApprovalDecision | null
  created_at: string; expires_at: string; consumed_at: string | null; valid: boolean; stale_reason: string | null
  scope: { tool_name: 'text.normalize'; action: 'transform_resource'; environment_id: string; resource: string; identity: 'bees_user'; parameters: Record<string, string | number> }
  target: ResourceTarget; tool_name: 'text.normalize'; tool_version: '1'; operation: 'trim' | 'upper' | 'lower'
  input_hash: string; input_bytes: number; input_preview: string | null
}
type DecisionBody = { client_request_id: string; expected_revision: number; decision: ApprovalDecision; reason: ''; rule_name: null }
type RevokeBody = { client_request_id: string; expected_revision: number; status: 'revoked' }
export interface ResourceIntent { agentId: string; taskId: string; approvalId: string; signature: string; operation: 'decide' | 'revoke'; body: DecisionBody | RevokeBody }
const positive = (value: unknown): value is number => count(value) && value > 0
const nonNil = (value: unknown): value is string => uuid(value) && value === value.toLowerCase() && value !== '00000000-0000-0000-0000-000000000000'
const hash = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
const choices = ['allow_once', 'allow_rule', 'ask', 'deny']
const targetKeys = ['installation_id', 'environment_id', 'resource_id', 'adapter', 'agent_id', 'identity']
const parameterKeys = ['operation', 'input_hash', 'installation_id', 'plugin_id', 'plugin_revision', 'manifest_hash', 'grant_id', 'grant_revision', 'agent_revision', 'tool_version']
const keys = ['kind', 'id', 'agent_id', 'task_id', 'run_id', 'task_title', 'intent_action_id', 'effect_action_id', 'effect_status', 'revision', 'status', 'decision', 'created_at', 'expires_at', 'consumed_at', 'valid', 'stale_reason', 'scope', 'target', 'tool_name', 'tool_version', 'operation', 'input_hash', 'input_bytes', 'input_preview']
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (record(value)) return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  return JSON.stringify(value)
}
async function digest(value: unknown) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value)))
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
export function checkedResourceApproval(value: unknown, agentId: string, taskId: string, approvalId?: string): ResourceApproval {
  if (!exact(value, keys) || value.kind !== 'resource_tool' || value.agent_id !== agentId || value.task_id !== taskId || !nonNil(value.id) || approvalId !== undefined && value.id !== approvalId
    || !['run_id', 'intent_action_id', 'effect_action_id'].every((key) => nonNil(value[key])) || !positive(value.revision)
    || !['pending', 'approved', 'denied', 'revoked'].includes(String(value.status)) || !(value.decision === null || choices.includes(String(value.decision)))
    || typeof value.task_title !== 'string' || !value.task_title.trim() || Array.from(value.task_title).length > 200
    || !timestamp(value.created_at) || !timestamp(value.expires_at) || !(value.consumed_at === null || timestamp(value.consumed_at))
    || typeof value.valid !== 'boolean' || !(value.stale_reason === null || typeof value.stale_reason === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(value.stale_reason))
    || value.valid !== (value.stale_reason === null) || value.valid && (!['pending', 'approved'].includes(String(value.status)) || value.consumed_at !== null)
    || !(value.effect_status === null || actionStatuses.includes(value.effect_status as typeof actionStatuses[number]))
    || ['dispatch_started', 'confirmed', 'failed_no_effect', 'outcome_unknown'].includes(String(value.effect_status)) && value.consumed_at === null
    || value.tool_name !== 'text.normalize' || value.tool_version !== '1' || !['trim', 'upper', 'lower'].includes(String(value.operation)) || !hash(value.input_hash) || !count(value.input_bytes) || value.input_bytes > 8192
    || !(value.input_preview === null || typeof value.input_preview === 'string' && Array.from(value.input_preview).length <= 4096 && !value.input_preview.includes('\0') && new TextEncoder().encode(value.input_preview).byteLength === value.input_bytes)
    || !exact(value.target, targetKeys) || value.target.adapter !== 'local_process_text' || value.target.identity !== 'bees_user' || value.target.agent_id !== agentId
    || !nonNil(value.target.installation_id) || !nonNil(value.target.resource_id) || value.target.environment_id !== value.target.resource_id
    || !exact(value.scope, ['tool_name', 'action', 'environment_id', 'resource', 'identity', 'parameters']) || value.scope.tool_name !== 'text.normalize' || value.scope.action !== 'transform_resource' || value.scope.identity !== 'bees_user'
    || value.scope.environment_id !== value.target.resource_id || value.scope.resource !== `resource:${value.target.resource_id}` || !(exact(value.scope.parameters, parameterKeys) || exact(value.scope.parameters, [...parameterKeys, 'skill_execution_hash']) || exact(value.scope.parameters, [...parameterKeys, 'skill_execution_hash', 'routine_scope_hash']))) throw new ApiError('invalid_response')
  const parameters = value.scope.parameters
  if (parameters.operation !== value.operation || parameters.input_hash !== value.input_hash || parameters.installation_id !== value.target.installation_id || parameters.tool_version !== '1'
    || !nonNil(parameters.plugin_id) || !nonNil(parameters.grant_id) || !hash(parameters.manifest_hash) || !positive(parameters.plugin_revision) || !positive(parameters.grant_revision) || !positive(parameters.agent_revision)
    || 'skill_execution_hash' in parameters && !hash(parameters.skill_execution_hash) || 'routine_scope_hash' in parameters && !hash(parameters.routine_scope_hash)) throw new ApiError('invalid_response')
  return value as unknown as ResourceApproval
}
function base(agentId: string) {
  if (!nonNil(agentId)) throw new ApiError('invalid_request')
  return `/agents/${agentId}/tool-approvals`
}
export async function getResourceApprovals(agentId: string, taskId: string, offset = 0, signal?: AbortSignal): Promise<{ approvals: ResourceApproval[]; has_more: boolean }> {
  if (!nonNil(taskId) || !count(offset)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}?task_id=${taskId}&limit=50&offset=${offset}`, { authenticated: true, signal })
  if (!exact(value, ['approvals', 'has_more']) || !Array.isArray(value.approvals) || value.approvals.length > 50 || typeof value.has_more !== 'boolean' || value.has_more && value.approvals.length !== 50) throw new ApiError('invalid_response')
  const approvals = value.approvals.map((item) => checkedResourceApproval(item, agentId, taskId))
  if (new Set(approvals.map((item) => item.id)).size !== approvals.length || approvals.some((item) => item.input_preview !== null)) throw new ApiError('invalid_response')
  return { approvals, has_more: value.has_more }
}
async function checkedPreview(value: ResourceApproval) {
  if (value.input_preview !== null && await digest({ operation: value.operation, text: value.input_preview }) !== value.input_hash) throw new ApiError('invalid_response')
  if (value.valid && value.input_preview === null) throw new ApiError('invalid_response')
  return value
}
export async function getResourceApproval(agentId: string, taskId: string, approvalId: string, signal?: AbortSignal) {
  if (!nonNil(taskId) || !nonNil(approvalId)) throw new ApiError('invalid_request')
  return checkedPreview(checkedResourceApproval(await request(`${base(agentId)}/${approvalId}`, { authenticated: true, signal }), agentId, taskId, approvalId))
}
export function resourceSignature(value: ResourceApproval) {
  return canonical({ scope: value.scope, target: value.target, intent_action_id: value.intent_action_id, effect_action_id: value.effect_action_id })
}
export function checkedResourceIntent(value: unknown, agentId: string, taskId: string, approvalId: string): ResourceIntent {
  if (!exact(value, ['agentId', 'taskId', 'approvalId', 'signature', 'operation', 'body']) || value.agentId !== agentId || value.taskId !== taskId || value.approvalId !== approvalId
    || !nonNil(agentId) || !nonNil(taskId) || !nonNil(approvalId) || typeof value.signature !== 'string' || value.signature.length > 6000 || !record(value.body)
    || !nonNil(value.body.client_request_id) || !positive(value.body.expected_revision)
    || !(value.operation === 'decide' && exact(value.body, ['client_request_id', 'expected_revision', 'decision', 'reason', 'rule_name']) && choices.includes(String(value.body.decision)) && value.body.reason === '' && value.body.rule_name === null
      || value.operation === 'revoke' && exact(value.body, ['client_request_id', 'expected_revision', 'status']) && value.body.status === 'revoked')) throw new ApiError('invalid_request')
  return value as unknown as ResourceIntent
}
export function resourceIntent(source: ResourceApproval, decision: ApprovalDecision | 'revoke'): ResourceIntent {
  if (decision === 'revoke' ? !['pending', 'approved'].includes(source.status) : source.status !== 'pending' || !source.valid || source.consumed_at !== null) throw new ApiError('invalid_request')
  return checkedResourceIntent({ agentId: source.agent_id, taskId: source.task_id, approvalId: source.id, signature: resourceSignature(source), operation: decision === 'revoke' ? 'revoke' : 'decide',
    body: { client_request_id: crypto.randomUUID(), expected_revision: source.revision, ...(decision === 'revoke' ? { status: 'revoked' } : { decision, reason: '', rule_name: null }) } }, source.agent_id, source.task_id, source.id)
}
export async function sendResourceDecision(intent: ResourceIntent, signal?: AbortSignal) {
  checkedResourceIntent(intent, intent.agentId, intent.taskId, intent.approvalId)
  const current = await getResourceApproval(intent.agentId, intent.taskId, intent.approvalId, signal)
  if (resourceSignature(current) !== intent.signature) throw new ApiError('invalid_request')
  const value = await request(`${base(intent.agentId)}/${intent.approvalId}${intent.operation === 'decide' ? '/decision' : ''}`, { authenticated: true, method: intent.operation === 'revoke' ? 'PATCH' : undefined, body: intent.body, signal })
  if (!exact(value, ['approval', 'receipt']) || !exact(value.receipt, ['approval_id', 'client_request_id', 'operation', 'expected_revision', 'request_hash', 'confirmed']) || value.receipt.approval_id !== intent.approvalId
    || value.receipt.client_request_id !== intent.body.client_request_id || value.receipt.operation !== intent.operation || value.receipt.expected_revision !== intent.body.expected_revision || value.receipt.confirmed !== true
    || value.receipt.request_hash !== await digest({ operation: intent.operation, input: intent.body })) throw new ApiError('invalid_response')
  const approval = await checkedPreview(checkedResourceApproval(value.approval, intent.agentId, intent.taskId, intent.approvalId))
  if (approval.revision <= intent.body.expected_revision || resourceSignature(approval) !== intent.signature) throw new ApiError('invalid_response')
  return approval
}
