import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { processSkillIntent, sendSkill } from './skills'
import { agentId, definition, skill } from '../features/skills/fixtures'

const otherId = '99999999-9999-4999-8999-999999999999'
const source = { task_id: skill.id, run_id: skill.id, message_id: skill.id, result_sha256: 'a'.repeat(64) }
const review = { sources: ['https://example.com/manual', 'https://example.com/limits'], review_note: 'Conferido à mão', pending: 'Reavaliar recursos', confirmed: true }
const intent = () => processSkillIntent(agentId, structuredClone(definition), structuredClone(source), structuredClone(review))
const origin = () => ({ skill_id: skill.id, version: 1, agent_id: agentId, ...source, review: structuredClone(review), created_at: skill.current.created_at, actor: 'user' })
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function mockOrigin(value: unknown) {
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => Response.json(options.method === 'POST' ? skill : value))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

describe('immutable process origin confirmation', () => {
  it('confirms the returned revision through an authenticated read before success', async () => {
    const value = origin()
    value.review = { confirmed: true, pending: review.pending, sources: [...review.sources], review_note: review.review_note }
    const fetcher = mockOrigin(value)
    expect(await sendSkill(intent())).toEqual(skill)
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/agents/${agentId}/skills/${skill.id}/revisions/1/process`)
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
    expect(fetcher.mock.calls[1][1].body).toBeUndefined()
  })
  it.each(['absent', 'task_id', 'run_id', 'message_id', 'result_sha256', 'sources', 'sources_order', 'review_note', 'pending', 'confirmed', 'skill_id', 'version', 'agent_id', 'actor', 'extra'])('rejects incompatible %s without automatic replay', async (field) => {
    const value = origin()
    if (field === 'task_id' || field === 'run_id' || field === 'message_id' || field === 'skill_id' || field === 'agent_id') value[field] = otherId
    if (field === 'result_sha256') value.result_sha256 = 'b'.repeat(64)
    if (field === 'sources') value.review.sources = ['https://example.com/other']
    if (field === 'sources_order') value.review.sources.reverse()
    if (field === 'review_note') value.review.review_note = 'Outra revisão'
    if (field === 'pending') value.review.pending = 'Outros limites'
    if (field === 'confirmed') value.review.confirmed = false
    if (field === 'version') value.version = 2
    if (field === 'actor') value.actor = 'model'
    const fetcher = mockOrigin(field === 'absent' ? null : field === 'extra' ? { ...value, grants: [] } : value)
    await expect(sendSkill(intent())).rejects.toMatchObject({ code: 'invalid_response', status: 0 })
    expect(fetcher.mock.calls.map(([, options]) => options.method)).toEqual(['POST', 'GET'])
  })
  it.each([400, 401, 403, 404, 409, 422, 500])('keeps uncertainty after origin lookup HTTP %s', async (status) => {
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? Response.json(skill) : Response.json({ error: { code: 'not_found' } }, { status }))
    vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(intent())).rejects.toMatchObject({ code: 'invalid_response', status: 0 })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('keeps uncertainty after a network failure and only replays by explicit call', async () => {
    let failed = true
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') return Response.json(skill)
      if (failed) throw new TypeError('offline')
      return Response.json(origin())
    }); vi.stubGlobal('fetch', fetcher)
    const command = intent()
    await expect(sendSkill(command)).rejects.toMatchObject({ code: 'invalid_response', status: 0 })
    expect(fetcher).toHaveBeenCalledTimes(2)
    failed = false
    expect(await sendSkill(command)).toEqual(skill)
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[2][1].body)
  })
  it.each([false, true])('compares snapshot when caller mutates during origin read (altered=%s)', async (altered) => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? Response.json(skill) : new Promise<Response>((resolve) => { finish = resolve }))
    vi.stubGlobal('fetch', fetcher)
    const command = intent(); const pending = sendSkill(command)
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
    const changedReview = command.body.review as typeof review
    changedReview.sources[0] = 'https://example.com/changed'; changedReview.pending = 'Alterado'
    command.body.task_id = otherId; command.agentId = otherId; command.path = '/tasks'
    const value = origin()
    if (altered) { value.review = structuredClone(changedReview); value.task_id = otherId }
    finish(Response.json(value))
    if (altered) await expect(pending).rejects.toMatchObject({ code: 'invalid_response' })
    else expect(await pending).toEqual(skill)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it.each([false, true])('captures source and human review before POST (altered=%s)', async (altered) => {
    let finish!: (response: Response) => void
    const command = intent(); const original = structuredClone(command.body)
    const value = origin()
    const fetcher = vi.fn((_url: string, options: RequestInit) => options.method === 'POST'
      ? new Promise<Response>((resolve) => { finish = resolve }) : Promise.resolve(Response.json(value)))
    vi.stubGlobal('fetch', fetcher)
    const pending = sendSkill(command)
    command.body.message_id = otherId
    const changedReview = command.body.review as typeof review
    changedReview.sources.reverse(); changedReview.review_note = 'Revisão substituída'
    if (altered) { value.message_id = otherId; value.review = structuredClone(changedReview) }
    finish(Response.json(skill))
    if (altered) await expect(pending).rejects.toMatchObject({ code: 'invalid_response' })
    else expect(await pending).toEqual(skill)
    expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).toEqual(original)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('treats malformed origin JSON as uncertainty after POST', async () => {
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? Response.json(skill) : new Response('broken JSON', { status: 200 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(intent())).rejects.toMatchObject({ code: 'invalid_response', status: 0 })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it('passes cancellation to origin lookup and never replays', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') return Response.json(skill)
      controller.abort()
      expect(options.signal?.aborted).toBe(true)
      throw new DOMException('Aborted', 'AbortError')
    }); vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(intent(), controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
})
