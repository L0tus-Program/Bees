import type { ActivityItem, ActivityPage } from '../../api/activity'
import type { AgentSummary } from '../../api/onboarding'
export const agent: AgentSummary = { id: '11111111-1111-4111-8111-111111111111', name: 'Abelha de teste', purpose: 'Validar atividade', instructions: '', provider_config: null, conversation_id: null, revision: 1, status: 'active', memory_enabled: true }
export const taskId = '22222222-2222-4222-8222-222222222222'
export const runId = '33333333-3333-4333-8333-333333333333'
export const actionId = '44444444-4444-4444-8444-444444444444'
export const item: ActivityItem = { id: '55555555-5555-4555-8555-555555555555', seq: 10, occurred_at: '2026-10-12T11:00:00Z', entity_type: 'task', entity_id: taskId, task_id: taskId, task_title: 'Revisar relatório', run_id: runId, action_id: null, status: 'paused', revision: 2, error_code: 'budget_exhausted', environment: { kind: 'textual', environment_id: null }, usage: null }
export const page: ActivityPage = {
  agent_id: agent.id, snapshot: { event_seq: 10, observed_at: '2026-10-12T12:00:00Z' }, scope: { task_id: null, run_id: null }, interval: { since: '2026-10-11T12:00:00Z', until: '2026-10-12T12:00:00Z' }, unit: 'tokens',
  usage: { counted_tokens: 1500, reported_tokens: 700, estimated_tokens: 100, reserved_tokens: 300, unknown_tokens: 400, released_entries: 1, entries: 5, open_reservations: 1, unknown_entries: 1, estimate_methods: ['chars_div_3_v1'] },
  operations: { model_calls_in_flight: 1, tool_actions_in_flight: 0, chat_reservations: 0, unknown_model_calls: 1, unknown_tool_actions: 0, waiting_approval: 0, waiting_resource: 1 }, items: [item], next_before: null,
}
