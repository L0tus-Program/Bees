import type { DeliveryItem, DeliveryPage, DeliveryScope } from '../../api/deliveryHistory'
export { agentId, taskId, conversationId, artifact } from '../artifacts/fixtures'
import { agentId, taskId, conversationId } from '../artifacts/fixtures'
export const result: DeliveryItem = {
  kind: 'result', id: '88888888-8888-4888-8888-888888888888', created_at: '2025-01-01T12:00:00+00:00', agent_id: agentId,
  task_id: taskId, conversation_id: conversationId, title: 'Resultado antigo', run_id: '44444444-4444-4444-8444-444444444444',
  version: 1, revision: 1, status: 'completed', partial: true, summary: 'Resumo preservado',
}
export const scope: DeliveryScope = { query: '', task_id: null, conversation_id: null }
export function page(items: DeliveryItem[] = [result], next_cursor: string | null = null): DeliveryPage {
  return { agent_id: agentId, scope, items, next_cursor, limit: 50, until: '2026-10-16T12:00:00+00:00' }
}
