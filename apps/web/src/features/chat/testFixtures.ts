import type { AgentSummary } from '../../api/onboarding'
import type { TaskRecord } from '../../api/tasks'

export const stamp = '2026-10-07T18:00:00Z'
export const agent: AgentSummary & { conversation_id: string } = {
  id: 'bee-1', name: 'Aurora', purpose: 'Organizar ideias', instructions: '', conversation_id: 'chat-1', revision: 1, status: 'active', memory_enabled: true,
  provider_config: { kind: 'openai_compatible', endpoint: 'https://model.invalid/v1', model: 'fixture', capabilities: { text: true, tool_calls: true } },
}
export const task: TaskRecord = {
  id: 'task-1', agent_id: agent.id, conversation_id: 'task-chat-1', title: 'Redação sobre acesso à cultura', objective: 'Explique as barreiras', expected_result: 'Uma redação', status: 'completed', revision: 3,
  created_at: stamp, updated_at: stamp, active_run_id: 'run-1', control_requested: null, available_controls: [],
  latest_run: { id: 'run-1', status: 'completed', provider: 'openai_compatible', model: 'fixture', started_at: stamp, finished_at: stamp, error_code: null, result: { content: 'Texto de teste.\n\nA cultura deve ser acessível a todos.', created_at: stamp } },
  progress: null, unknown_requires_ack: false, unknown_model_calls: 0, unknown_tool_actions: 0, action_in_flight: false,
}
export const delegation = { id: task.id, source_message_id: 'message-user', response_message_id: 'message-assistant', task }
