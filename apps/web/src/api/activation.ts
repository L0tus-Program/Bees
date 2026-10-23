import { ApiError, record, request } from './client'
import { uuid } from '../features/artifacts/api'
export interface ReactivationReceipt { agent_id: string; revision: number; status: 'active'; safety_revision: number; global_status: 'stopped'; provider_checked: true }
export interface ReactivationCommand { expected_revision: number; expected_safety_revision: number; consent: 'reactivate_paused_agent' }
const revision = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 1
export async function reactivateAgent(agentId: string, body: ReactivationCommand, signal?: AbortSignal): Promise<ReactivationReceipt> {
  if (!uuid(agentId) || !revision(body.expected_revision) || !revision(body.expected_safety_revision) || body.consent !== 'reactivate_paused_agent') throw new ApiError('invalid_request')
  const value = await request(`/agents/${agentId}/reactivate`, { body, authenticated: true, signal, timeoutMs: 60000 })
  if (!record(value) || Object.keys(value).length !== 6 || value.agent_id !== agentId || !uuid(value.agent_id)
    || !revision(value.revision) || value.revision !== body.expected_revision + 1 || value.status !== 'active'
    || value.safety_revision !== body.expected_safety_revision || value.global_status !== 'stopped' || value.provider_checked !== true) throw new ApiError('invalid_response')
  return value as unknown as ReactivationReceipt
}
