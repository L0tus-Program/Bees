import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { checkedSkillTaskIntent, createSkillTask, getSkillContext } from './skillTasks'
import { digest } from './toolTasks'
import { setCsrfToken } from './client'
import { runnableSkill, skillTaskIntent, skillTaskReceipt } from '../features/skills/skillTaskFixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
it('binds the receipt to the complete human intent and fixed skill version', async () => {
  const fetcher = vi.fn(async () => Response.json(skillTaskReceipt())); vi.stubGlobal('fetch', fetcher)
  const task = await createSkillTask(skillTaskIntent)
  expect(task.skill_execution?.version).toBe(1)
  expect(fetcher.mock.calls).toHaveLength(1)
})
it.each(['client_request_id', 'request_hash', 'agent', 'skill', 'version', 'snapshot', 'budget'])('refuses a mismatched %s receipt', async (field) => {
  const receipt = skillTaskReceipt()
  if (field === 'client_request_id') receipt.client_request_id = crypto.randomUUID()
  if (field === 'request_hash') receipt.request_hash = 'b'.repeat(64)
  if (field === 'agent') receipt.task.agent_id = crypto.randomUUID()
  if (field === 'skill') receipt.task.skill_execution.skill_id = crypto.randomUUID()
  if (field === 'version') receipt.task.skill_execution.version++
  if (field === 'snapshot') receipt.task.skill_execution.snapshot_hash = ''
  if (field === 'budget') receipt.task.max_calls = 99
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(receipt)))
  await expect(createSkillTask(skillTaskIntent)).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each([{ approved: true }, { max_calls: 5 }, { inputs: { texto: '\0' } }, { inputs: { texto: 'x'.repeat(2049) } }])('rejects altered authority or limits before HTTP', (extra) => {
  expect(() => checkedSkillTaskIntent({ ...skillTaskIntent, body: { ...skillTaskIntent.body, ...extra } }, skillTaskIntent.agentId)).toThrow()
})
it('reads saved private inputs only explicitly and verifies their complete snapshot hash', async () => {
  const definition = runnableSkill.current.definition
  const snapshot = { skill_id: runnableSkill.id, version: 1, definition, definition_hash: await digest(definition), inputs: { texto: 'Olá' } }
  const snapshot_hash = await digest(snapshot)
  const binding = { skill_id: runnableSkill.id, version: 1, snapshot_hash }
  const fetcher = vi.fn(async () => Response.json({ snapshot, snapshot_hash })); vi.stubGlobal('fetch', fetcher)
  const context = await getSkillContext(skillTaskIntent.agentId, skillTaskReceipt().task.id, binding)
  expect(context.inputs).toEqual({ texto: 'Olá' })
  snapshot.inputs.texto = 'Alterado'
  await expect(getSkillContext(skillTaskIntent.agentId, skillTaskReceipt().task.id, binding)).rejects.toMatchObject({ code: 'invalid_response' })
})

const mutations = {
  agent: (intent: typeof skillTaskIntent) => { intent.agentId = crypto.randomUUID() },
  request: (intent: typeof skillTaskIntent) => { intent.body.client_request_id = crypto.randomUUID() },
  skill: (intent: typeof skillTaskIntent) => { intent.body.skill_id = crypto.randomUUID() },
  version: (intent: typeof skillTaskIntent) => { intent.body.skill_version++ },
  inputs: (intent: typeof skillTaskIntent) => { intent.body.inputs.texto = 'Outro conteúdo' },
  body: (intent: typeof skillTaskIntent) => { intent.body = { ...intent.body, inputs: { texto: 'Corpo substituído' }, skill_version: 2 } },
}
it.each(Object.entries(mutations).flatMap(([field, mutate]) => [false, true].map((altered) => ({ field, mutate, altered }))))('preserves the sent $field despite caller mutation (altered receipt=$altered)', async ({ mutate, altered }) => {
  const intent = structuredClone(skillTaskIntent)
  let deliver!: (response: Response) => void
  const fetcher = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(() => new Promise<Response>((resolve) => { deliver = resolve }))
  vi.stubGlobal('fetch', fetcher)
  const pending = createSkillTask(intent)
  const assertion = altered ? expect(pending).rejects.toMatchObject({ code: 'invalid_response' }) : expect(pending).resolves.toMatchObject({ agent_id: skillTaskIntent.agentId })
  mutate(intent)
  const receipt = skillTaskReceipt(altered ? intent.body : skillTaskIntent.body)
  receipt.task.agent_id = altered ? intent.agentId : skillTaskIntent.agentId
  deliver(Response.json(receipt))
  await assertion
  expect(fetcher).toHaveBeenCalledOnce()
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${skillTaskIntent.agentId}/skill-tasks`)
  expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).toEqual(skillTaskIntent.body)
})
it.each((['agent', 'skill', 'version', 'body'] as const).flatMap((field) => [false, true].map((altered) => ({ field, altered }))))('keeps the original $field while the receipt hash is pending (altered receipt=$altered)', async ({ field, altered }) => {
  const intent = structuredClone(skillTaskIntent)
  let hashing!: () => void
  const started = new Promise<void>((resolve) => { hashing = resolve })
  let finish!: () => void
  const gate = new Promise<void>((resolve) => { finish = resolve })
  vi.stubGlobal('crypto', { randomUUID: () => webcrypto.randomUUID(), subtle: { digest: async (algorithm: string, bytes: BufferSource) => { hashing(); await gate; return webcrypto.subtle.digest(algorithm, bytes) } } })
  const changed = structuredClone(intent); mutations[field](changed)
  const receipt = skillTaskReceipt(altered ? changed.body : intent.body)
  receipt.task.agent_id = altered ? changed.agentId : intent.agentId
  // Keep the original request hash so identity/version checks after digest must run.
  receipt.request_hash = skillTaskReceipt().request_hash
  const fetcher = vi.fn(async () => Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
  const pending = createSkillTask(intent)
  const assertion = altered ? expect(pending).rejects.toMatchObject({ code: 'invalid_response' }) : expect(pending).resolves.toMatchObject({ agent_id: skillTaskIntent.agentId })
  await started
  Object.assign(intent, changed)
  finish()
  await assertion
  expect(fetcher).toHaveBeenCalledOnce()
})
