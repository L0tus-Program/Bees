// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { computerResources } from '../../locales/computer'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'
import { registerConfirmHost } from '../../components/confirmAction'
import { PersonalConnectors } from './PersonalConnectors'

const executor = { id: 'executor-1', kind: 'personal', name: 'Meu computador', status: 'active', revision: 1, online: true, last_seen_at: null, version: null, platform: null, created_at: '2026-11-04T07:00:00Z', capabilities: [], roots: ['Documentos'] }
const grant = { id: 'grant-1', agent_id: 'agent-1', enabled: true, revision: 2, scope: { roots: ['Documentos'], write: false, shell: false, browser: true } }
let container: HTMLDivElement, root: Root, fetcher: ReturnType<typeof vi.fn>, uncaught: unknown[]
beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container)
  uncaught = []; root = createRoot(container, { onUncaughtError: (error) => { uncaught.push(error) } })
  fetcher = vi.fn(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? grant : url.endsWith('/grants') ? { grants: [grant] } : { executors: [executor] }))
  vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); registerConfirmHost(null); vi.useRealTimers(); vi.unstubAllGlobals(); setCsrfToken()
})
async function show(agentId = "agent-1") {
  const instance = createInstance()
  await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { computer: computerResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={instance}><PersonalConnectors agentId={agentId} /></I18nextProvider>))
}
const save = () => [...container.querySelectorAll('button')].find((item) => item.textContent === computerResources['pt-BR'].saveGrant)!
const puts = () => fetcher.mock.calls.filter(([, init]) => (init as RequestInit).method === 'PUT')

it.each([
  [{ ...grant, scope: { ...grant.scope, roots: 1 } }],
  [{ ...grant, enabled: 'false' }],
  [grant, { ...grant, id: 'grant-2' }],
].map((grants) => ({ grants })))('shows a controlled error and blocks saving an invalid initial grant snapshot: %j', async ({ grants }) => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith('/grants') ? { grants } : { executors: [executor] }))
  await show()
  expect(uncaught).toEqual([])
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(save().disabled).toBe(true)
  await act(async () => save().click())
  expect(puts()).toHaveLength(0)
})
it('reports a foreign-agent receipt and does not repeat the save on polling', async () => {
  await show()
  fetcher.mockImplementation(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? { ...grant, agent_id: 'other' } : url.endsWith('/grants') ? { grants: [grant] } : { executors: [executor] }))
  await act(async () => save().click())
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(puts()).toHaveLength(1)
  await act(async () => vi.advanceTimersByTimeAsync(15000))
  expect(puts()).toHaveLength(1)
  expect(uncaught).toEqual([])
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
const boxes = () => [...container.querySelectorAll<HTMLInputElement>('.connector-grant input')]
it('resets the draft and revision while the next agent snapshot loads', async () => {
  await show()
  const pending = deferred<Response>()
  fetcher.mockImplementation(async (url: string) => url.endsWith('/grants') ? pending.promise : Response.json({ executors: [executor] }))
  await show('agent-2')
  expect(save().disabled).toBe(true)
  expect(boxes().every((input) => !input.checked)).toBe(true)
  await act(async () => pending.resolve(Response.json({ grants: [] })))
  expect(save().disabled).toBe(false)
  expect(boxes().every((input) => !input.checked)).toBe(true)
})
it('ignores the previous agent read after a new snapshot arrives', async () => {
  const old = deferred<Response>(), current = deferred<Response>()
  let reads = 0
  fetcher.mockImplementation(async (url: string) => url.endsWith('/grants') ? (++reads === 1 ? old.promise : current.promise) : Response.json({ executors: [executor] }))
  await show()
  await show('agent-2')
  await act(async () => current.resolve(Response.json({ grants: [] })))
  await act(async () => old.resolve(Response.json({ grants: [grant] })))
  expect(boxes().every((input) => !input.checked)).toBe(true)
})
it('serializes same-turn saves and disables edits until receipt', async () => {
  await show()
  const pending = deferred<Response>()
  fetcher.mockImplementation(async () => pending.promise)
  await act(async () => { save().click(); save().click() })
  expect(puts()).toHaveLength(1)
  expect(boxes().every((input) => input.disabled || input.closest('fieldset')?.disabled)).toBe(true)
  await act(async () => pending.resolve(Response.json(grant)))
})
it.each([true, false])('ignores saves settling after the agent changes (success=%s)', async (success) => {
  await show()
  const pending = deferred<Response>()
  fetcher.mockImplementation(async (url: string, init: RequestInit) => init.method === 'PUT' ? pending.promise : Response.json(url.endsWith('/grants') ? { grants: [] } : { executors: [executor] }))
  await act(async () => save().click())
  await show('agent-2')
  const before = fetcher.mock.calls.length
  await act(async () => pending.resolve(Response.json(success ? grant : { error: { code: 'revision_conflict' } }, { status: success ? 200 : 409 })))
  expect(fetcher.mock.calls).toHaveLength(before)
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(boxes().every((input) => !input.checked)).toBe(true)
})
it('uses the receipt revision without an extra grant read', async () => {
  await show()
  const before = fetcher.mock.calls.filter(([url]) => (url as string).endsWith('/grants')).length
  fetcher.mockImplementation(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? { ...grant, revision: 3 } : url.endsWith('/grants') ? { grants: [grant] } : { executors: [executor] }))
  await act(async () => save().click())
  await act(async () => save().click())
  expect(JSON.parse(puts()[1][1].body as string).expected_revision).toBe(3)
  expect(fetcher.mock.calls.filter(([url]) => (url as string).endsWith('/grants'))).toHaveLength(before)
})
it('reconciles a failed save without hiding its error or repeating the mutation', async () => {
  await show()
  fetcher.mockImplementation(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? { error: { code: 'revision_conflict' } } : url.endsWith('/grants') ? { grants: [{ ...grant, revision: 4, enabled: false }] } : { executors: [executor] }, { status: init.method === 'PUT' ? 409 : 200 }))
  await act(async () => save().click())
  expect(puts()).toHaveLength(1)
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(boxes()[0].checked).toBe(false)
  fetcher.mockImplementation(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? { ...grant, revision: 5 } : url.endsWith('/grants') ? { grants: [grant] } : { executors: [executor] }))
  await act(async () => save().click())
  expect(JSON.parse(puts()[1][1].body as string).expected_revision).toBe(4)
  expect(container.querySelector('[role="alert"]')).toBeNull()
})
it('blocks another write when reconciliation also fails', async () => {
  await show()
  fetcher.mockImplementation(async () => Response.json({}, { status: 503 }))
  await act(async () => save().click())
  expect(save().disabled).toBe(true)
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  await act(async () => save().click())
  expect(puts()).toHaveLength(1)
})
it('saves the empty new-agent grant with its own initial revision', async () => {
  await show()
  fetcher.mockImplementation(async (url: string, init: RequestInit) => Response.json(init.method === 'PUT' ? { ...grant, agent_id: 'agent-2' } : url.endsWith('/grants') ? { grants: [] } : { executors: [executor] }))
  await show('agent-2')
  await act(async () => save().click())
  expect(puts()[0][0]).toContain('/agent-2')
  expect(JSON.parse(puts()[0][1].body as string)).toEqual({ expected_revision: 0, enabled: false, roots: [], write: false, shell: false, browser: false })
})
it('ignores an old read failure after changing agent', async () => {
  const pending = deferred<Response>()
  fetcher.mockImplementation(async (url: string) => url.endsWith('/grants') ? pending.promise : Response.json({ executors: [executor] }))
  await show()
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith('/grants') ? { grants: [] } : { executors: [executor] }))
  await show('agent-2')
  await act(async () => pending.resolve(Response.json({}, { status: 503 })))
  expect(save().disabled).toBe(false)
  expect(container.querySelector('[role="alert"]')).toBeNull()
})
it('starts a new form when polling replaces the connector', async () => {
  await show()
  const pending = deferred<Response>()
  fetcher.mockImplementation(async (url: string) => url.endsWith('/grants') ? pending.promise : Response.json({ executors: [{ ...executor, id: 'executor-2' }] }))
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(save().disabled).toBe(true)
  expect(boxes().every((input) => !input.checked)).toBe(true)
  await act(async () => pending.resolve(Response.json({ grants: [] })))
  expect(save().disabled).toBe(false)
})

it('shows a controlled error for malformed connector roots without mounting a grant form', async () => {
  fetcher.mockImplementation(async () => Response.json({ executors: [{ ...executor, roots: [1] }] }))
  await show()
  expect(uncaught).toEqual([])
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(container.querySelector('.connector-grant')).toBeNull()
})

const pairButton = () => [...container.querySelectorAll('button')].find((item) => item.textContent === computerResources['pt-BR'].pairPersonal)!
const revokeButton = () => [...container.querySelectorAll('button')].find((item) => item.textContent === computerResources['pt-BR'].revoke)!
const posts = () => fetcher.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST')
it.each([true, false])('ignores older list results (success=%s)', async (success) => {
  await show()
  const old = deferred<Response>()
  fetcher.mockImplementationOnce(async () => old.promise)
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  fetcher.mockImplementation(async () => Response.json({ executors: [] }))
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  await act(async () => old.resolve(Response.json(success ? { executors: [executor] } : {}, { status: success ? 200 : 503 })))
  expect(container.querySelector('.connector-card')).toBeNull()
  expect(container.querySelector('[role="alert"]')).toBeNull()
})
it('clears a recovered list error', async () => {
  fetcher.mockImplementation(async () => Response.json({}, { status: 503 }))
  await show()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  fetcher.mockImplementation(async () => Response.json({ executors: [] }))
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(container.querySelector('[role="alert"]')).toBeNull()
})
it('serializes invitations and suspends polling until completion', async () => {
  await show()
  const pending = deferred<Response>()
  fetcher.mockImplementation(async () => pending.promise)
  await act(async () => { pairButton().click(); pairButton().click() })
  expect(posts()).toHaveLength(1)
  const before = fetcher.mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  expect(fetcher.mock.calls).toHaveLength(before)
  await act(async () => pending.resolve(Response.json({ token: 'one-time', expires_at: '2026-11-04T09:00:00Z' })))
  expect(container.querySelector('.invite-token')?.textContent).toBe('one-time')
  expect(pairButton().disabled).toBe(false)
})
it('locks revocation before confirmation and rejects competing actions', async () => {
  await show()
  const decision = deferred<boolean>()
  const confirm = vi.fn(({ resolve }: { resolve: (value: boolean) => void }) => { void decision.promise.then(resolve) })
  registerConfirmHost(confirm)
  await act(async () => { revokeButton().click(); revokeButton().click(); pairButton().click() })
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(posts()).toHaveLength(0)
  await act(async () => decision.resolve(false))
  expect(pairButton().disabled).toBe(false)
  expect(posts()).toHaveLength(0)
})
it('does not dispatch a confirmed revoke after unmount', async () => {
  await show()
  const decision = deferred<boolean>()
  registerConfirmHost(({ resolve }) => { void decision.promise.then(resolve) })
  await act(async () => revokeButton().click())
  await act(async () => root.render(null))
  await act(async () => decision.resolve(true))
  expect(posts()).toHaveLength(0)
})
it('does not resurrect a revoked connector from a pre-mutation read', async () => {
  await show()
  const old = deferred<Response>()
  fetcher.mockImplementationOnce(async () => old.promise)
  await act(async () => vi.advanceTimersByTimeAsync(5000))
  registerConfirmHost(({ resolve }) => resolve(true))
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => Response.json(init.method === 'POST' ? { ...executor, status: 'revoked', revision: 2 } : { executors: [] }))
  await act(async () => revokeButton().click())
  expect(container.querySelector('.connector-card')).toBeNull()
  await act(async () => old.resolve(Response.json({ executors: [executor] })))
  expect(container.querySelector('.connector-card')).toBeNull()
  expect(posts()).toHaveLength(1)
})
it('keeps a revoked connector removed when reconciliation fails', async () => {
  await show()
  registerConfirmHost(({ resolve }) => resolve(true))
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => Response.json(init.method === 'POST' ? { ...executor, status: 'revoked', revision: 2 } : {}, { status: init.method === 'POST' ? 200 : 503 }))
  await act(async () => revokeButton().click())
  expect(container.querySelector('.connector-card')).toBeNull()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
})
it('preserves mutation errors across successful polling without repeating POST', async () => {
  await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => Response.json(init.method === 'POST' ? {} : { executors: [] }, { status: init.method === 'POST' ? 503 : 200 }))
  await act(async () => pairButton().click())
  await act(async () => vi.advanceTimersByTimeAsync(10000))
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(posts()).toHaveLength(1)
})
it('does not reconcile a revoke settling after unmount', async () => {
  await show()
  registerConfirmHost(({ resolve }) => resolve(true))
  const pending = deferred<Response>()
  fetcher.mockImplementation(async () => pending.promise)
  await act(async () => revokeButton().click())
  await act(async () => root.render(null))
  const before = fetcher.mock.calls.length
  await act(async () => pending.resolve(Response.json({ ...executor, status: 'revoked', revision: 2 })))
  expect(fetcher.mock.calls).toHaveLength(before)
})
