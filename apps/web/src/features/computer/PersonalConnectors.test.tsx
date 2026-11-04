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
import { PersonalConnectors } from './PersonalConnectors'

const executor = { id: 'executor-1', kind: 'personal', name: 'Meu computador', status: 'active', revision: 1, online: true, capabilities: [], roots: ['Documentos'] }
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
  await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); setCsrfToken()
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
