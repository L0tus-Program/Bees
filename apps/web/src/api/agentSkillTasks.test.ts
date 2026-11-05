import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createAgentSkillTask, checkedAgentSkillTaskIntent } from './skillTasks'
import { setCsrfToken } from './client'
import { agentSkillTaskIntent, agentSkillTaskReceipt } from '../features/skills/skillTaskFixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
it.each([false, true])('normalizes omitted budgets and accepts exact receipts (custom=%s)', async (custom) => {
  const body = { ...agentSkillTaskIntent.body, max_calls: custom ? 1 : 20, max_active_seconds: custom ? 30 : 900 }
  const raw = { ...body } as Partial<typeof body>
  if (!custom) { delete raw.max_calls; delete raw.max_active_seconds }
  const fetcher = vi.fn(async () => Response.json(agentSkillTaskReceipt(body))); vi.stubGlobal('fetch', fetcher)
  await expect(createAgentSkillTask(agentSkillTaskIntent.agentId, raw as typeof body)).resolves.toMatchObject({ max_calls: body.max_calls })
  expect(fetcher).toHaveBeenCalledOnce()
})
it.each(['request', 'hash', 'agent', 'skill', 'version', 'snapshot', 'calls', 'seconds', 'kind', 'agentApproval', 'modelApproval', 'toolApproval', 'task', 'conversation', 'run', 'extra'])('rejects incompatible %s receipt', async (field) => {
  const receipt = agentSkillTaskReceipt()
  if (field === 'request') receipt.client_request_id = crypto.randomUUID()
  if (field === 'hash') receipt.request_hash = 'b'.repeat(64)
  if (field === 'agent') receipt.task.agent_id = crypto.randomUUID()
  if (field === 'skill') receipt.task.skill_execution.skill_id = crypto.randomUUID()
  if (field === 'version') receipt.task.skill_execution.version++
  if (field === 'snapshot') receipt.task.skill_execution.snapshot_hash = 'z'.repeat(64)
  if (field === 'calls') receipt.task.max_calls++
  if (field === 'seconds') receipt.task.max_active_seconds++
  if (field === 'kind') receipt.task.execution_kind = 'text_task'
  if (field === 'agentApproval') receipt.task.agent_approvals_available = false
  if (field === 'modelApproval') receipt.task.model_approvals_available = false
  if (field === 'toolApproval') receipt.task.tool_approvals_available = true
  if (field === 'task') receipt.task.id = 'invalid'
  if (field === 'conversation') receipt.task.conversation_id = null as unknown as string
  if (field === 'run') receipt.task.active_run_id = '00000000-0000-0000-0000-000000000000'
  if (field === 'extra') Object.assign(receipt, { approved: true })
  const fetcher = vi.fn(async () => Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
  await expect(createAgentSkillTask(agentSkillTaskIntent.agentId, agentSkillTaskIntent.body)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledOnce()
})
it.each([{ max_calls: 0 }, { max_calls: 51 }, { max_calls: 1.5 }, { max_active_seconds: 29 }, { max_active_seconds: 1801 }, { max_calls: null }, { approved: true }, { skill_version: 0 }, { inputs: { texto: '\0' } }])('blocks invalid request before HTTP: %j', async (extra) => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  const intent = { ...agentSkillTaskIntent, body: { ...agentSkillTaskIntent.body, ...extra } }
  expect(() => checkedAgentSkillTaskIntent(intent, intent.agentId)).toThrow()
  await expect(createAgentSkillTask(intent.agentId, intent.body as typeof agentSkillTaskIntent.body)).rejects.toMatchObject({ code: 'invalid_request' })
  expect(fetcher).not.toHaveBeenCalled()
})
it.each(['request', 'skill', 'version', 'inputs', 'calls', 'seconds'] as const)('keeps sent %s immutable during POST and rejects altered receipt', async (field) => {
  for (const altered of [false, true]) {
    const intent = structuredClone(agentSkillTaskIntent)
    let deliver!: (value: Response) => void
    const fetcher = vi.fn<(url: string, options: RequestInit) => Promise<Response>>(() => new Promise((resolve) => { deliver = resolve })); vi.stubGlobal('fetch', fetcher)
    const result = createAgentSkillTask(intent.agentId, intent.body)
    const assertion = altered ? expect(result).rejects.toMatchObject({ code: 'invalid_response' }) : expect(result).resolves.toMatchObject({ execution_kind: 'agent_task' })
    if (field === 'request') intent.body.client_request_id = crypto.randomUUID()
    if (field === 'skill') intent.body.skill_id = crypto.randomUUID()
    if (field === 'version') intent.body.skill_version++
    if (field === 'inputs') intent.body.inputs.texto = 'Outro texto'
    if (field === 'calls') intent.body.max_calls++
    if (field === 'seconds') intent.body.max_active_seconds++
    deliver(Response.json(agentSkillTaskReceipt(altered ? intent.body : agentSkillTaskIntent.body)))
    await assertion
    expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).toEqual(agentSkillTaskIntent.body)
    expect(fetcher).toHaveBeenCalledOnce()
  }
})
it('keeps skill version and budgets fixed while SHA-256 is pending', async () => {
  const intent = structuredClone(agentSkillTaskIntent)
  let hashing!: () => void; const started = new Promise<void>((resolve) => { hashing = resolve })
  let finish!: () => void; const gate = new Promise<void>((resolve) => { finish = resolve })
  vi.stubGlobal('crypto', { subtle: { digest: async (algorithm: string, bytes: BufferSource) => { hashing(); await gate; return webcrypto.subtle.digest(algorithm, bytes) } } })
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(agentSkillTaskReceipt())))
  const result = createAgentSkillTask(intent.agentId, intent.body)
  await started
  intent.body.skill_version++; intent.body.max_calls++; intent.body.max_active_seconds++
  finish()
  await expect(result).resolves.toMatchObject({ max_calls: 20, skill_execution: { version: 1 } })
})
