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
async function show() {
  const instance = createInstance()
  await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { computer: computerResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={instance}><PersonalConnectors agentId="agent-1" /></I18nextProvider>))
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
