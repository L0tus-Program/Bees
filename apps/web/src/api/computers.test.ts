import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { createComputer, getComputers, removeComputer, restartComputer, setComputerControl } from './computers'
import type { Computer } from './computers'

const computer: Computer = {
  id: 'computer-1', agent_id: 'agent-1', executor_id: 'executor-1', name: 'Computador',
  status: 'ready', revision: 3, control_holder: 'agent', control_revision: 1,
  reason_code: null, online: true, created_at: '2026-11-03T12:00:00Z', updated_at: '2026-11-03T12:00:00Z',
}
const state = { computers: [computer], runtime_online: true, runtime_paired: true, local_link_available: false }
beforeEach(() => setCsrfToken('test-csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function response(value: unknown) {
  const fetcher = vi.fn(async () => Response.json(value))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

it.each([
  { agent_id: 'other' }, { id: '' }, { agent_id: '' }, { executor_id: '' }, { name: null },
  { status: 'unknown' }, { status: ['ready'] }, { control_holder: 'other' },
  { revision: 0 }, { revision: -1 }, { revision: 0.5 }, { revision: Number.MAX_SAFE_INTEGER + 1 },
  { control_revision: 0 }, { control_revision: true }, { control_revision: Number.MAX_SAFE_INTEGER + 1 },
  { online: 'true' }, { reason_code: {} }, { reason_code: undefined }, { created_at: null }, { updated_at: 1 },
])('rejects a malformed or foreign computer in a list: %j', async (patch) => {
  response({ ...state, computers: [{ ...computer, ...patch }] })
  await expect(getComputers(computer.agent_id)).rejects.toMatchObject({ code: 'invalid_response' })
})

it.each([
  { computers: [computer, { ...computer, agent_id: 'other' }] },
  { computers: [computer, computer] }, { computers: [null] }, { computers: {} },
  { runtime_online: null }, { runtime_paired: undefined }, { runtime_paired: 'false' },
  { local_link_available: undefined }, { local_link_available: 'false' },
])('rejects an inconsistent computer snapshot: %j', async (patch) => {
  response({ ...state, ...patch })
  await expect(getComputers(computer.agent_id)).rejects.toMatchObject({ code: 'invalid_response' })
})

const operations = {
  create: () => createComputer(computer.agent_id, computer.id),
  remove: () => removeComputer(computer),
  control: () => setComputerControl(computer, 'human'),
  restart: () => restartComputer(computer, 'request-1'),
}
it.each(Object.keys(operations) as (keyof typeof operations)[])('rejects a foreign agent in a %s receipt without retry', async (operation) => {
  const fetcher = response({ ...computer, agent_id: 'other', restarted: true })
  await expect(operations[operation]()).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each(['remove', 'control', 'restart'] as const)('rejects a foreign computer or executor in a %s receipt without retry', async (operation) => {
  for (const patch of [{ id: 'other' }, { executor_id: 'other' }]) {
    const fetcher = response({ ...computer, ...patch, restarted: true })
    await expect(operations[operation]()).rejects.toMatchObject({ code: 'invalid_response' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  }
})
it('binds the created computer to the idempotency identifier chosen by the caller', async () => {
  const fetcher = response({ ...computer, id: 'other' })
  await expect(operations.create()).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each([undefined, false, 'true'])('requires an explicit restart confirmation: %j', async (restarted) => {
  const fetcher = response({ ...computer, restarted })
  await expect(operations.restart()).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('accepts valid lifecycle states, nullable reasons and empty offline snapshots', async () => {
  for (const status of ['provisioning', 'ready', 'failed', 'removing', 'removed'] as const) {
    const value = { ...state, computers: [{ ...computer, status, reason_code: status === 'failed' ? 'executor_revoked' : null }] }
    response(value)
    await expect(getComputers(computer.agent_id)).resolves.toEqual(value)
  }
  const empty = { ...state, computers: [], runtime_online: false, runtime_paired: false }
  response(empty)
  await expect(getComputers(computer.agent_id)).resolves.toEqual(empty)
})
it('accepts correctly bound mutation receipts and preserves CAS and request identifiers', async () => {
  for (const operation of Object.keys(operations) as (keyof typeof operations)[]) {
    const value = { ...computer, control_holder: 'human', restarted: true }
    const fetcher = response(value)
    await expect(operations[operation]()).resolves.toEqual(value)
    expect(fetcher).toHaveBeenCalledTimes(1)
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe(`/api/v1/agents/agent-1/computers${operation === 'create' ? '' : `/computer-1/${operation}`}`)
    expect(JSON.parse(String(init.body))).toEqual(operation === 'create' || operation === 'restart'
      ? { client_request_id: operation === 'create' ? computer.id : 'request-1' }
      : operation === 'remove' ? { expected_revision: 3 } : { expected_revision: 1, holder: 'human' })
  }
})
