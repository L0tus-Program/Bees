import { afterEach, describe, expect, it, vi } from 'vitest'
import { webcrypto } from 'node:crypto'
import { checkedDocument, checkedSkill, enableIntent, importIntent, listSkills, sendSkill, skillIntent, skillRevisions } from './skills'
import { setCsrfToken } from './client'
import { agentId, definition, skill } from '../features/skills/fixtures'

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('skill contracts and intent binding', () => {
  it('rejects cross-agent, stale revision binding and executable import fields', () => {
    const document = { format: 'bees.skill/v1', scope: 'agent', definition }
    expect(checkedDocument(document)).toEqual(document)
    for (const invalid of [{ ...document, code: 'exec()' }, { ...document, scope: 'global' }, { ...document, definition: { ...definition, steps: [{ title: 'x', instruction: 'y', script: 'exec()' }] } }, { ...document, definition: { ...definition, resources: { ...definition.resources, grants: ['write'] } } }]) expect(() => checkedDocument(invalid)).toThrow('invalid_request')
    for (const invalid of [{ ...skill, agent_id: skill.id }, { ...skill, enabled_version: 2 }, { ...skill, current: { ...skill.current, version: 2 } }, { ...skill, current: { ...skill.current, skill_id: agentId } }]) expect(() => checkedSkill(invalid, agentId)).toThrow('invalid_response')
  })
  it('reuses the same command UUID after a lost response and never enables on edit', async () => {
    vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
    const intent = skillIntent(agentId, definition, skill)
    expect(intent.body).toMatchObject({ expected_revision: 1, definition })
    expect(intent.body).not.toHaveProperty('enabled')
    const next = { ...skill, revision: 2, current_version: 2, current: { ...skill.current, version: 2 } }
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('Lost')).mockResolvedValueOnce(Response.json(next))
    vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(intent)).rejects.toThrow('mutation_network')
    expect(await sendSkill(intent)).toEqual(next)
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body)
    expect(enableIntent(skill).body).toMatchObject({ expected_revision: 1, version: 1, enabled: true })
    expect(importIntent(agentId, { format: 'bees.skill/v1', scope: 'agent', definition }).body).not.toHaveProperty('enabled')
  })
  it('checks paginated lists and the immutable revision identity', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([skill])).mockResolvedValueOnce(Response.json([skill, skill])).mockResolvedValueOnce(Response.json([{ ...skill.current, skill_id: agentId }]))
    vi.stubGlobal('fetch', fetcher)
    expect(await listSkills(agentId, 50)).toEqual([skill])
    expect(fetcher.mock.calls[0][0]).toContain('limit=50&offset=50')
    await expect(listSkills(agentId)).rejects.toThrow('invalid_response')
    await expect(skillRevisions(agentId, skill.id)).rejects.toThrow('invalid_response')
  })
})
