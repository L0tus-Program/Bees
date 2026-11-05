import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { enableIntent, importIntent, listSkills, processSkillIntent, sendSkill, skillIntent, skillRevisions } from './skills'
import { agentId, definition, skill } from '../features/skills/fixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })

// Generated independently with hashlib.sha256(json.dumps(definition, sort_keys=True,
// separators=(',', ':')).encode()).hexdigest(), matching SkillService._canonical.
const vectors = [
  ['ASCII only', 'b81fb18346986f596fbc4a769224c555e084623bb3fdca299e0c5487d5b08fdf'],
  ['Ação, café e referências', 'f079168b1a8e06ba889d33d8dc621dfc6ffae794e14a46e4b7404a66b7a1b15d'],
  ['中文 日本語', 'b48265a535f24f6287d7e512d2999ae732d9f76f0d133034e8f8879216b1ff62'],
  ['Abelha 🐝 e 😀', 'ee163806c78c7320f506c9fc93d50a0548fd3cbd53f237db2f999abbce863fd1'],
  ['Linha\nTab\tAspas " e barra \\', '6cf00b275f651d0e12cd3d289e01b83d70b06070466249eb53b8f2374acfdc2c'],
  ['DEL\u007f LS\u2028 PS\u2029', '67cc8aa01e9630e392faf6d26a4a435926292200dcfc4ced24dac356e12f473d'],
  ['e\u0301 ≠ é', '62512b34b82464dc829e182b78ea1f4143daac562b3074d2390476880b5a90c0'],
]

describe('immutable skill content hash', () => {
  it.each(vectors)('accepts the Python canonical hash for %s in list and history', async (purpose, content_hash) => {
    const current = { ...skill.current, content_hash, definition: { ...definition, purpose } }
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([{ ...skill, current }])).mockResolvedValueOnce(Response.json([current]))
    vi.stubGlobal('fetch', fetcher)
    expect(await listSkills(agentId)).toEqual([{ ...skill, current }])
    expect(await skillRevisions(agentId, skill.id)).toEqual([current])
  })
  it('ignores object key order', async () => {
    const current = { ...skill.current, definition: { completion: definition.completion, output: definition.output, resources: { tools: [...definition.resources.tools], environment: definition.resources.environment }, steps: structuredClone(definition.steps), inputs: structuredClone(definition.inputs), purpose: definition.purpose, name: definition.name } }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json([{ ...skill, current }])))
    expect(await listSkills(agentId)).toEqual([{ ...skill, current }])
  })
  it('does not normalize Unicode text to make an incompatible hash match', async () => {
    const current = { ...skill.current, content_hash: vectors[1][1], definition: { ...definition, purpose: vectors[1][0].normalize('NFD') } }
    vi.stubGlobal('fetch', vi.fn(async () => Response.json([{ ...skill, current }])))
    await expect(listSkills(agentId)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each(['hash', 'definition'])('rejects an inconsistent %s in a whole list or history page without retry', async (field) => {
    const current = structuredClone(skill.current)
    if (field === 'hash') current.content_hash = '0'.repeat(64)
    else current.definition.steps[0].instruction = 'Conteúdo substituído'
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([skill, { ...skill, id: agentId, current: { ...current, skill_id: agentId } }])).mockResolvedValueOnce(Response.json([{ ...current, version: 2 }, skill.current]))
    vi.stubGlobal('fetch', fetcher)
    await expect(listSkills(agentId)).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(skillRevisions(agentId, skill.id)).rejects.toMatchObject({ code: 'invalid_response' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  const commands = {
    create: () => skillIntent(agentId, definition),
    edit: () => skillIntent(agentId, definition, skill),
    import: () => importIntent(agentId, { format: 'bees.skill/v1', scope: 'agent', definition }),
    process: () => processSkillIntent(agentId, definition, { task_id: skill.id, run_id: skill.id, message_id: skill.id, result_sha256: 'a'.repeat(64) }, { sources: ['https://example.org/'], review_note: 'Conferido', pending: 'Nenhuma', confirmed: true }),
    enable: () => enableIntent(skill),
    disable: () => enableIntent({ ...skill, enabled_version: 1 }),
  }
  it.each(Object.keys(commands) as (keyof typeof commands)[])('rejects a wrong content hash after %s without another request', async (kind) => {
    const version = kind === 'edit' ? 2 : 1
    const receipt = { ...skill, revision: ['edit', 'enable', 'disable'].includes(kind) ? 2 : 1, current_version: version, enabled_version: kind === 'enable' ? 1 : null,
      current: { ...skill.current, version, origin: kind === 'import' ? 'import' : 'human', content_hash: '0'.repeat(64) } }
    const fetcher = vi.fn(async () => Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(commands[kind]())).rejects.toMatchObject({ code: 'invalid_response', status: 0 })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each(['list', 'history', 'save'] as const)('fails closed when SHA-256 fails during %s', async (kind) => {
    vi.spyOn(webcrypto.subtle, 'digest').mockRejectedValue(new Error('Private implementation detail'))
    const fetcher = vi.fn(async () => Response.json(kind === 'list' ? [skill] : kind === 'history' ? [skill.current] : skill)); vi.stubGlobal('fetch', fetcher)
    const pending = kind === 'list' ? listSkills(agentId) : kind === 'history' ? skillRevisions(agentId, skill.id) : sendSkill(skillIntent(agentId, definition))
    await expect(pending).rejects.toMatchObject({ code: 'invalid_response', status: 0, message: 'invalid_response' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
