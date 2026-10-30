import { createHash } from 'node:crypto'
import { task } from '../chat/testFixtures'
import { resourceIds } from '../approvals/resourceFixtures'
import type { ToolTaskBody, ToolTaskIntent } from '../../api/toolTasks'

export const toolTaskIntent: ToolTaskIntent = { agentId: resourceIds.agent, body: {
  client_request_id: resourceIds.intent, title: 'Conferir texto', objective: 'Transformar o texto próprio', expected_result: 'Resultado conferido', max_calls: 4, max_active_seconds: 120, deliverables: [],
} }
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (typeof value === 'object' && value !== null) return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : 1).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
export function toolTaskReceipt(body: ToolTaskBody = toolTaskIntent.body) {
  return { task: { ...task, id: resourceIds.task, agent_id: resourceIds.agent, conversation_id: resourceIds.run, active_run_id: resourceIds.effect,
    title: body.title, objective: body.objective, expected_result: body.expected_result, max_calls: 4, max_active_seconds: 120, tool_approvals_available: true, model_approvals_available: true },
  client_request_id: body.client_request_id, request_hash: createHash('sha256').update(canonical(body)).digest('hex') }
}
export const toolTaskCapabilities = { available: true, reason: null, tool_name: 'normalize_text', max_calls: 4, max_active_seconds: 120, max_actions: 16 }
