import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { importIntent, processSkillIntent, sendSkill, skillIntent } from './skills'
import type { Skill, SkillIntent } from './skills'
import { agentId, definition, skill } from '../features/skills/fixtures'
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
const factories = {
  create: () => skillIntent(agentId, structuredClone(definition)),
  revise: () => skillIntent(agentId, structuredClone(definition), skill),
  import: () => importIntent(agentId, { format: 'bees.skill/v1', scope: 'agent', definition: structuredClone(definition) }),
  process: () => processSkillIntent(agentId, structuredClone(definition), { task_id: skill.id, run_id: skill.id, message_id: skill.id, result_sha256: 'a'.repeat(64) }, { sources: ['https://example.com/'], review_note: 'Conferido', pending: 'Nenhuma', confirmed: true }),
}
function receipt(kind: keyof typeof factories): Skill {
  return { ...skill, revision: kind === 'revise' ? 2 : 1, current_version: kind === 'revise' ? 2 : 1,
    current: { ...skill.current, version: kind === 'revise' ? 2 : 1, origin: kind === 'import' ? 'import' : 'human', definition: structuredClone(definition) } }
}
function processOrigin(command: SkillIntent) {
  const { task_id, run_id, message_id, result_sha256, review } = command.body
  return { skill_id: skill.id, version: 1, agent_id: agentId, task_id, run_id, message_id, result_sha256, review, created_at: skill.current.created_at, actor: 'user' }
}
function sentDefinition(intent: SkillIntent) {
  return (intent.body.definition ?? (intent.body.document as { definition: typeof definition }).definition) as typeof definition
}
for (const kind of Object.keys(factories) as (keyof typeof factories)[]) describe(kind, () => {
  it('accepts an exact receipt with reordered object keys', async () => {
    const result = receipt(kind)
    result.current.definition = { completion: definition.completion, output: definition.output, resources: { tools: [...definition.resources.tools], environment: definition.resources.environment }, steps: structuredClone(definition.steps), inputs: structuredClone(definition.inputs), purpose: definition.purpose, name: definition.name }
    const command = factories[kind]()
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => Response.json(options.method === 'GET' ? processOrigin(command) : result)))
    expect(await sendSkill(command)).toEqual(result)
  })
  it.each(['name', 'input', 'step', 'resource', 'output', 'completion', 'enabled', 'origin', 'revision', 'version'])('rejects a substituted %s without retry', async (field) => {
    const result = receipt(kind)
    if (field === 'name') result.current.definition.name = 'Outro procedimento'
    if (field === 'input') result.current.definition.inputs[0].required = false
    if (field === 'step') result.current.definition.steps[0].instruction = 'Outra ação'
    if (field === 'resource') result.current.definition.resources.tools = ['shell.run']
    if (field === 'output') result.current.definition.output = 'Outra entrega'
    if (field === 'completion') result.current.definition.completion = 'Outro critério'
    if (field === 'enabled') result.enabled_version = result.current_version
    if (field === 'origin') result.current.origin = kind === 'import' ? 'human' : 'import'
    if (field === 'revision') result.revision += 2
    if (field === 'version') { result.current_version = 1; result.current.version = 1; if (kind !== 'revise') { result.revision = 2; result.current_version = 2; result.current.version = 2 } }
    const fetcher = vi.fn(async () => Response.json(result)); vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(factories[kind]())).rejects.toMatchObject({ code: 'invalid_response' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([false, true])('captures nested content before await (altered receipt=%s)', async (altered) => {
    const intent = factories[kind]()
    let finish!: (value: Response) => void
    const origin = processOrigin(structuredClone(intent))
    const fetcher = vi.fn((_url: string, options: RequestInit) => options.method === 'GET' ? Promise.resolve(Response.json(origin)) : new Promise<Response>((resolve) => { finish = resolve })); vi.stubGlobal('fetch', fetcher)
    const pending = sendSkill(intent)
    sentDefinition(intent).steps[0].instruction = 'Alterado durante envio'
    sentDefinition(intent).resources.tools.push('shell.run')
    const result = receipt(kind)
    if (altered) result.current.definition = structuredClone(sentDefinition(intent))
    finish(Response.json(result))
    if (altered) await expect(pending).rejects.toMatchObject({ code: 'invalid_response' })
    else expect(await pending).toEqual(result)
    expect(fetcher).toHaveBeenCalledTimes(kind === 'process' && !altered ? 2 : 1)
  })
})
it.each(['path', 'method', 'extra', 'definition', 'revision', 'document', 'process'])('rejects malformed %s before fetch', async (field) => {
  const intent = field === 'document' ? factories.import() : field === 'process' ? factories.process() : factories.create()
  if (field === 'path') intent.path = '/tasks'
  if (field === 'method') intent.method = 'PUT'
  if (field === 'extra') intent.body.enabled = true
  if (field === 'definition') intent.body.definition = { ...definition, code: 'execute' }
  if (field === 'revision') intent.body.expected_revision = 1
  if (field === 'document') (intent.body.document as Record<string, unknown>).scope = 'global'
  if (field === 'process') intent.body.review = { confirmed: false }
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  await expect(sendSkill(intent)).rejects.toMatchObject({ code: 'invalid_request' })
  expect(fetcher).not.toHaveBeenCalled()
})
