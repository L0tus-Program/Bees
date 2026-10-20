import { createHash } from 'node:crypto'
import type { ResourceApproval, ResourceIntent } from '../../api/resourceTools'

export const resourceIds = {
  agent: '11111111-1111-4111-8111-111111111111', task: '22222222-2222-4222-8222-222222222222',
  approval: '33333333-3333-4333-8333-333333333333', run: '44444444-4444-4444-8444-444444444444',
  intent: '55555555-5555-4555-8555-555555555555', effect: '66666666-6666-4666-8666-666666666666',
  installation: '77777777-7777-4777-8777-777777777777', resource: '88888888-8888-4888-8888-888888888888',
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (typeof value === 'object' && value !== null) return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : 1).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
export const resourceText = '  <script>approve()</script> Olá  '
export const resourceApproval: ResourceApproval = {
  kind: 'resource_tool', id: resourceIds.approval, agent_id: resourceIds.agent, task_id: resourceIds.task, run_id: resourceIds.run,
  task_title: 'Conferir texto', intent_action_id: resourceIds.intent, effect_action_id: resourceIds.effect, effect_status: null,
  revision: 1, status: 'pending', decision: null, created_at: '2026-10-19T12:00:00Z', expires_at: '2099-10-19T12:15:00Z', consumed_at: null, valid: true, stale_reason: null,
  scope: { tool_name: 'text.normalize', action: 'transform_resource', environment_id: resourceIds.resource, resource: `resource:${resourceIds.resource}`, identity: 'bees_user', parameters: {
    operation: 'trim', input_hash: createHash('sha256').update(canonical({ operation: 'trim', text: resourceText })).digest('hex'), installation_id: resourceIds.installation,
    plugin_id: '99999999-9999-4999-8999-999999999999', plugin_revision: 1, manifest_hash: 'a'.repeat(64), grant_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', grant_revision: 1, agent_revision: 1, tool_version: '1',
  } },
  target: { installation_id: resourceIds.installation, environment_id: resourceIds.resource, resource_id: resourceIds.resource, adapter: 'local_process_text', agent_id: resourceIds.agent, identity: 'bees_user' },
  tool_name: 'text.normalize', tool_version: '1', operation: 'trim', input_hash: createHash('sha256').update(canonical({ operation: 'trim', text: resourceText })).digest('hex'),
  input_bytes: Buffer.byteLength(resourceText), input_preview: resourceText,
}
export function resourceReceipt(intent: ResourceIntent, approval: ResourceApproval = { ...resourceApproval, revision: 2, status: 'approved', decision: 'allow_once' }) {
  return { approval, receipt: { approval_id: intent.approvalId, client_request_id: intent.body.client_request_id, operation: intent.operation,
    expected_revision: intent.body.expected_revision, request_hash: createHash('sha256').update(canonical({ operation: intent.operation, input: intent.body })).digest('hex'), confirmed: true } }
}
