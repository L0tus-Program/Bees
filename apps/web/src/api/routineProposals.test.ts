import { afterEach, expect, it, vi } from 'vitest'
import { getRoutineProposal, proposalDefinition } from './routineProposals'
import { agentId, conversationId, occurrence, definition, skill } from '../features/routines/fixtures'

const source = { conversation_id: conversationId, message_id: occurrence.id, content_hash: 'a'.repeat(64) }
const proposal = { name: definition.name, instructions: definition.instructions, schedule: definition.schedule, timezone: definition.timezone, inputs: { ...definition.inputs, extraneous: 'never copied' }, notification: definition.notification, grace_seconds: 60 }
afterEach(() => vi.unstubAllGlobals())
it('reads a scoped declarative suggestion, derives authority from the human-selected skill and saves no action', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ source, proposal })); vi.stubGlobal('fetch', fetcher)
  const result = await getRoutineProposal(agentId, conversationId, occurrence.id)
  const draft = proposalDefinition(result, skill)
  expect(draft).toMatchObject({ skill_id: skill.id, skill_version: skill.current_version, environment: skill.current.definition.resources.environment, execution_mode: 'prepare', runtime_binding: null })
  expect(draft.inputs).not.toHaveProperty('extraneous')
  expect(fetcher.mock.calls[0][1].method).toBe('GET')
})
it('rejects foreign sources, extra authority and malformed proposal contracts', async () => {
  for (const value of [{ source: { ...source, conversation_id: agentId }, proposal }, { source, proposal: { ...proposal, active: true } }, { source, proposal: { ...proposal, environment: 'personal' } }, { source, proposal: { ...proposal, inputs: { secret: 'sk-or-v1-' + 'x'.repeat(30) } } }]) {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getRoutineProposal(agentId, conversationId, occurrence.id)).rejects.toThrow('invalid_response')
  }
})
