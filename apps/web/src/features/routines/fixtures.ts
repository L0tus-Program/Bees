import type { Routine, RoutineDefinition, RoutineOccurrence, RoutineRevision } from '../../api/routines'
import { definition as skillDefinition, skill as baseSkill, agentId } from '../skills/fixtures'
export { agentId }
export const conversationId = '33333333-3333-4333-8333-333333333333'
export const skill = { ...baseSkill, revision: 2, enabled_version: 1 }
export const definition: RoutineDefinition = {
  name: 'Resumo diário', instructions: 'Conferir as fontes e resumir para revisão humana.',
  schedule: { frequency: 'daily', time: '09:00', weekdays: [] }, timezone: 'America/Sao_Paulo', skill_id: skill.id, skill_version: 1,
  inputs: { tema: 'Ciência' }, environment: skillDefinition.resources.environment, destination: { kind: 'conversation', conversation_id: conversationId },
  notification: 'always', grace_seconds: 60, misfire_policy: 'skip', overlap_policy: 'skip',
}
export const routine: Routine = { id: '44444444-4444-4444-8444-444444444444', agent_id: agentId, name: definition.name, instructions: definition.instructions, timezone: definition.timezone, schedule: definition.schedule, misfire_policy: 'skip', overlap_policy: 'skip',
  next_run_at: null, status: 'paused', created_at: '2026-10-16T11:00:00Z', updated_at: '2026-10-16T11:00:00Z', revision: 1, metadata: {}, origin: 'human', definition,
}
export const persistedRoutine = routine
export const activeRoutine: Routine = { ...persistedRoutine, status: 'active', next_run_at: '2026-10-17T12:00:00Z' }
export const revision: RoutineRevision = { routine_id: routine.id, revision: 1, definition, origin: 'human', created_at: routine.created_at }
export const occurrence: RoutineOccurrence = { id: '55555555-5555-4555-8555-555555555555', routine_id: routine.id, routine_revision: 1,
  scheduled_at: '2026-10-16T12:00:00Z', window_end: '2026-10-16T12:00:00Z', status: 'skipped', reason: 'misfire', task_id: null, revision: 1,
  created_at: '2026-10-16T13:00:00Z', updated_at: '2026-10-16T13:00:00Z', metadata: {},
}
