import { ApiError, request } from './client'
import { exact, uuid } from './activity'
import { isRoutineDefinition, isRoutineProposalSource } from './routines'
import type { RoutineDefinition, RoutineProposalSource } from './routines'
import type { Skill } from './skills'

export interface RoutineSuggestion { name: string; instructions: string; schedule: RoutineDefinition['schedule']; timezone: string; inputs: Record<string, string>; notification: RoutineDefinition['notification']; grace_seconds: number }
export interface RoutineProposal { source: RoutineProposalSource; proposal: RoutineSuggestion }
export async function getRoutineProposal(agentId: string, conversationId: string, messageId: string, signal?: AbortSignal): Promise<RoutineProposal> {
  if (![agentId, conversationId, messageId].every((id) => uuid(id) && id !== '00000000-0000-0000-0000-000000000000')) throw new ApiError('invalid_request')
  const value = await request(`/agents/${agentId}/routines/proposals/${conversationId}/${messageId}`, { authenticated: true, signal })
  if (!exact(value, ['source', 'proposal']) || !isRoutineProposalSource(value.source) || value.source.conversation_id !== conversationId || value.source.message_id !== messageId
    || !exact(value.proposal, ['name', 'instructions', 'schedule', 'timezone', 'inputs', 'notification', 'grace_seconds'])
    || !isRoutineDefinition({ ...value.proposal, skill_id: messageId, skill_version: 1, environment: 'none', destination: { kind: 'conversation', conversation_id: conversationId }, misfire_policy: 'skip', overlap_policy: 'skip' })) throw new ApiError('invalid_response')
  return value as unknown as RoutineProposal
}
export function proposalDefinition(proposal: RoutineProposal, skill: Skill): RoutineDefinition {
  const allowed = new Set(skill.current.definition.inputs.map((input) => input.name))
  return { ...proposal.proposal, inputs: Object.fromEntries(Object.entries(proposal.proposal.inputs).filter(([name]) => allowed.has(name))), skill_id: skill.id, skill_version: skill.current_version, environment: skill.current.definition.resources.environment,
    destination: { kind: 'conversation', conversation_id: proposal.source.conversation_id }, misfire_policy: 'skip', overlap_policy: 'skip', execution_mode: 'prepare', runtime_binding: null }
}
