// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { computerResources } from '../../locales/computer'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'
import type { Computer } from '../../api/computers'
import { confirmAction } from '../../components/confirmAction'
import { ComputerWorkspace } from './ComputerWorkspace'

const screen = vi.hoisted(() => ({ mounts: 0 }))
vi.mock('./ScreenViewer', () => ({ ScreenViewer: function Viewer() {
  useEffect(() => { screen.mounts += 1 }, [])
  return <div>tela</div>
} }))
vi.mock('./PersonalConnectors', () => ({ PersonalConnectors: () => null }))
vi.mock('../../components/confirmAction', () => ({ confirmAction: vi.fn(async () => true) }))
const computer: Computer = {
  id: 'computer-1', agent_id: 'agent-1', executor_id: 'executor-1', name: 'PC', status: 'ready', revision: 3,
  control_holder: 'agent', control_revision: 1, reason_code: null, online: true, created_at: '', updated_at: '',
}
const snapshot = (items: Computer[] = [computer]) => Response.json({ computers: items, runtime_online: true, runtime_paired: true, local_link_available: false })
const failure = (code = 'computer_restart_failed') => Response.json({ error: { code, message: 'x' } }, { status: 409 })
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
let root: Root, container: HTMLDivElement, fetcher: ReturnType<typeof vi.fn>
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  fetcher = vi.fn(async () => snapshot()); vi.stubGlobal('fetch', fetcher)
  vi.mocked(confirmAction).mockReset().mockResolvedValue(true); screen.mounts = 0
})
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); setCsrfToken()
})
async function show() {
  const instance = createInstance()
  await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { computer: computerResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={instance}><ComputerWorkspace agentId="agent-1" /></I18nextProvider>))
}
const button = (key: keyof typeof computerResources['pt-BR']) => [...container.querySelectorAll('button')].find((item) => item.textContent === computerResources['pt-BR'][key])!
const posts = () => fetcher.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST')
const restartIds = () => posts().filter(([url]) => String(url).endsWith('/restart')).map(([, init]) => JSON.parse((init as RequestInit).body as string).client_request_id)
const tick = () => act(async () => vi.advanceTimersByTimeAsync(4000))
const click = (key: keyof typeof computerResources['pt-BR']) => act(async () => button(key).click())

it('waits for the active periodic read instead of overlapping or starving it', async () => {
  await show()
  const slow = deferred<Response>(); fetcher.mockImplementationOnce(() => slow.promise)
  await tick(); const calls = fetcher.mock.calls.length
  await tick(); await tick()
  expect(fetcher.mock.calls).toHaveLength(calls)
  await act(async () => slow.resolve(snapshot([{ ...computer, control_holder: 'human' }])))
  expect(button('returnControl')).toBeTruthy()
  await tick(); expect(fetcher.mock.calls).toHaveLength(calls + 1)
})

it.each(['success', 'failure'] as const)('ignores an old %s read across takeover and pauses polling during the action', async (outcome) => {
  await show()
  const old = deferred<Response>(), control = deferred<Response>()
  fetcher.mockImplementationOnce(() => old.promise); await tick()
  const human = { ...computer, control_holder: 'human' as const, control_revision: 2 }
  fetcher.mockImplementation((url: string) => String(url).endsWith('/control') ? control.promise : Promise.resolve(snapshot([human])))
  await click('takeControl'); const calls = fetcher.mock.calls.length
  await tick(); await tick(); expect(fetcher.mock.calls).toHaveLength(calls)
  await act(async () => control.resolve(Response.json(human)))
  expect(button('returnControl')).toBeTruthy()
  await act(async () => old.resolve(outcome === 'success' ? snapshot() : failure()))
  expect(button('returnControl')).toBeTruthy()
  expect(container.querySelector('[role="alert"]')).toBeNull()
})

it('sends a single control mutation for clicks in the same render', async () => {
  await show()
  const pending = deferred<Response>(); fetcher.mockImplementation(() => pending.promise)
  await act(async () => { button('takeControl').click(); button('takeControl').click() })
  expect(posts()).toHaveLength(1)
  await act(async () => pending.resolve(failure()))
})

it.each(['computer_restart_timeout', 'mutation_timeout', 'mutation_network', 'invalid_response', 'http_502'] as const)('reuses the restart ID after %s without automatic retry and reconnects only on success', async (code) => {
  await show(); await click('showScreen'); expect(screen.mounts).toBe(1)
  let nextFailure = true
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => {
    if (init.method !== 'POST') return snapshot()
    if (!nextFailure) return Response.json({ ...computer, restarted: true })
    if (code === 'mutation_network') throw new TypeError('connection lost')
    if (code === 'invalid_response') return Response.json({ ...computer, restarted: false })
    if (code === 'http_502') return Response.json({ error: { code } }, { status: 502 })
    return failure(code)
  })
  await click('restart')
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(screen.mounts).toBe(1)
  await tick(); expect(restartIds()).toHaveLength(1)
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  nextFailure = false; await click('restart')
  const ids = restartIds(); expect(ids).toHaveLength(2); expect(ids[0]).toBe(ids[1])
  expect(screen.mounts).toBe(2)
  await tick(); expect(screen.mounts).toBe(2)
  await click('restart'); expect(restartIds()[2]).not.toBe(ids[0])
})

it.each(['computer_restart_failed', 'computer_not_provisioned'])('starts a new restart request after terminal runtime failure %s', async (code) => {
  await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? failure(code) : snapshot())
  await click('restart'); await click('restart')
  expect(restartIds()).toHaveLength(2); expect(restartIds()[0]).not.toBe(restartIds()[1])
})

it('does not reuse a pending restart ID for a replacement computer', async () => {
  await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? failure('computer_restart_timeout') : snapshot())
  await click('restart')
  const replacement = { ...computer, id: 'computer-2' }
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? Response.json({ ...replacement, restarted: true }) : snapshot([replacement]))
  await tick(); await click('restart')
  expect(restartIds()).toHaveLength(2); expect(restartIds()[0]).not.toBe(restartIds()[1])
})

it('does not reconnect the screen just because the control revision changes in a poll', async () => {
  await show(); await click('showScreen')
  fetcher.mockImplementation(async () => snapshot([{ ...computer, control_revision: 2 }]))
  await tick(); expect(screen.mounts).toBe(1)
})

it.each(['control', 'restart', 'remove', 'create'] as const)('preserves a failed %s action when reads recover', async (operation) => {
  if (operation === 'create') fetcher.mockImplementation(async () => snapshot([]))
  await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? failure() : snapshot(operation === 'create' ? [] : [computer]))
  await click(operation === 'control' ? 'takeControl' : operation)
  const alert = container.querySelector('[role="alert"]')?.textContent
  expect(alert).toContain('Não foi possível reiniciar o computador')
  await tick(); expect(container.querySelector('[role="alert"]')?.textContent).toBe(alert)
  expect(posts()).toHaveLength(1)
})

it('clears a recovered read error without clearing an action error', async () => {
  fetcher.mockImplementationOnce(async () => failure('backend_unavailable'))
  await show(); expect(container.querySelector('[role="alert"]')).not.toBeNull()
  await tick(); expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(button('takeControl')).toBeTruthy(); expect(posts()).toHaveLength(0)
})

it.each(['restart', 'remove'] as const)('does not act on a %s confirmation resolved after unmount', async (operation) => {
  await show()
  const confirmation = deferred<boolean>(); vi.mocked(confirmAction).mockReturnValueOnce(confirmation.promise)
  await click(operation); await act(async () => root.render(null))
  await act(async () => confirmation.resolve(true))
  expect(posts()).toHaveLength(0)
})

it('does not fetch again after a pending mutation resolves on a closed workspace', async () => {
  await show(); const pending = deferred<Response>()
  fetcher.mockImplementation(() => pending.promise)
  await click('restart'); await act(async () => root.render(null))
  const calls = fetcher.mock.calls.length
  await act(async () => pending.resolve(Response.json({ ...computer, restarted: true })))
  expect(fetcher.mock.calls).toHaveLength(calls)
})

it.each(['restart', 'remove'] as const)('opens only one %s confirmation and sends no mutation on cancellation', async (operation) => {
  await show()
  const confirmation = deferred<boolean>(); vi.mocked(confirmAction).mockReturnValue(confirmation.promise)
  await act(async () => { button(operation).click(); button(operation).click() })
  expect(confirmAction).toHaveBeenCalledTimes(1)
  expect(posts()).toHaveLength(0)
  await act(async () => confirmation.resolve(false))
  expect(posts()).toHaveLength(0)
  vi.mocked(confirmAction).mockResolvedValue(false)
  await click(operation); expect(confirmAction).toHaveBeenCalledTimes(2)
})

it('serializes creation before the next render and refreshes only after the receipt', async () => {
  fetcher.mockImplementation(async () => snapshot([])); await show()
  const creation = deferred<Response>()
  fetcher.mockImplementation((_url: string, init: RequestInit) => init.method === 'POST' ? creation.promise : Promise.resolve(snapshot()))
  await act(async () => { button('create').click(); button('create').click() })
  expect(posts()).toHaveLength(1)
  const calls = fetcher.mock.calls.length
  await tick(); expect(fetcher.mock.calls).toHaveLength(calls)
  const requestId = JSON.parse((posts()[0][1] as RequestInit).body as string).client_request_id
  fetcher.mockImplementation(async () => snapshot([{ ...computer, id: requestId }]))
  await act(async () => creation.resolve(Response.json({ ...computer, id: requestId })))
  expect(button('takeControl')).toBeTruthy()
  expect(posts()).toHaveLength(1)
})

it('recovers periodic read errors while retaining the action error and last valid snapshot', async () => {
  await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? failure() : snapshot())
  await click('restart'); const actionError = container.querySelector('[role="alert"]')?.textContent
  fetcher.mockImplementation(async () => failure('backend_unavailable'))
  await tick(); expect(container.querySelectorAll('[role="alert"]')).toHaveLength(2)
  expect(button('takeControl')).toBeTruthy()
  fetcher.mockImplementation(async () => snapshot())
  await tick(); expect(container.querySelectorAll('[role="alert"]')).toHaveLength(1)
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(actionError)
  expect(posts()).toHaveLength(1)
})


it.each(['executor_offline', 'computer_unavailable'])('retains an uncertain restart across a later %s refusal', async (code) => {
  await show()
  let attempts = 0
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => {
    if (init.method !== 'POST') return snapshot()
    attempts += 1
    if (attempts === 1) return failure('computer_restart_timeout')
    if (attempts === 2) return failure(code)
    return Response.json({ ...computer, restarted: true })
  })
  await click('restart'); await click('restart'); await click('restart')
  expect(restartIds()).toHaveLength(3)
  expect(new Set(restartIds()).size).toBe(1)
})

it.each(['agent', 'human'] as const)('reconciles an inconsistent control receipt without announcing success or retrying (initial owner=%s)', async (holder) => {
  const source = { ...computer, control_holder: holder }
  fetcher.mockImplementation(async () => snapshot([source])); await show()
  fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST'
    ? Response.json({ ...source, control_revision: 2 }) : snapshot([source]))
  await click(holder === 'agent' ? 'takeControl' : 'returnControl')
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(container.querySelector('.success-notice')).toBeNull()
  expect(button(holder === 'agent' ? 'takeControl' : 'returnControl')).toBeTruthy()
  expect(posts()).toHaveLength(1)
  const reads = fetcher.mock.calls.length
  await tick(); expect(fetcher.mock.calls.length).toBeGreaterThan(reads)
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(posts()).toHaveLength(1)
})
