// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { Computer } from '../../api/computers'
import { setCsrfToken } from '../../api/client'
import { productResources } from '../../locales/product'
import { workspaceResources } from '../../locales/workspace'
import { ComputerPanel } from './ComputerPanel'

vi.mock('../computer/ScreenViewer', () => ({ ScreenViewer: () => <div>tela</div> }))

const computer: Computer = {
  id: 'computer-1', agent_id: 'agent-1', executor_id: 'executor-1', name: 'Computador', status: 'ready', revision: 3,
  control_holder: 'agent', control_revision: 1, reason_code: null, online: true, created_at: '2026-10-30T12:00:00Z', updated_at: '2026-10-30T12:00:00Z',
}
let root: Root, container: HTMLDivElement, fetcher: ReturnType<typeof vi.fn>
let finish: ((response: Response) => void) | null

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  finish = null
  fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/restart')) return new Promise<Response>((resolve) => { finish = resolve })
    return Response.json({ computers: [computer], runtime_online: true, runtime_paired: true, local_link_available: false })
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })

async function show() {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { workspace: workspaceResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ComputerPanel agentId="agent-1" name="Ana" /></I18nextProvider>))
}
const button = (label: string) => [...container.querySelectorAll('button')].find((item) => item.textContent === label)
const restarts = () => fetcher.mock.calls.filter(([url]) => String(url).endsWith('/restart'))

describe('computer panel restart', () => {
  it('asks for confirmation, shows progress and sends one restart request', async () => {
    await show()
    await act(async () => button('Reiniciar')!.click())
    expect(container.textContent).toContain('Navegador e programas abertos serão fechados')
    expect(restarts()).toHaveLength(0)
    await act(async () => button('Cancelar')!.click())
    expect(container.textContent).not.toContain('Navegador e programas abertos serão fechados')

    await act(async () => button('Reiniciar')!.click())
    await act(async () => button('Reiniciar agora')!.click())
    expect(restarts()).toHaveLength(1)
    const [url, init] = restarts()[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/agents/agent-1/computers/computer-1/restart')
    expect(JSON.parse(String(init.body)).client_request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect((init.headers as Record<string, string>)['X-Bees-CSRF']).toBe('csrf')
    const progress = button('Reiniciando…')!
    expect(progress.disabled).toBe(true)
    expect(button('Assumir controle')!.disabled).toBe(true)

    await act(async () => { finish!(Response.json({ ...computer, restarted: true })); await new Promise((resolve) => setTimeout(resolve, 5)) })
    expect(button('Reiniciar')!.disabled).toBe(false)
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('explains a failed restart', async () => {
    await show()
    await act(async () => button('Reiniciar')!.click())
    await act(async () => button('Reiniciar agora')!.click())
    await act(async () => { finish!(Response.json({ error: { code: 'computer_restart_failed', message: 'x' } }, { status: 409 })); await new Promise((resolve) => setTimeout(resolve, 5)) })
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Não foi possível reiniciar o computador')
  })
})

function deferredResponse() {
  let resolve!: (response: Response) => void
  const promise = new Promise<Response>((done) => { resolve = done })
  return { promise, resolve }
}
const snapshot = (holder: Computer['control_holder'] = 'agent') => Response.json({
  computers: [{ ...computer, control_holder: holder }], runtime_online: true, runtime_paired: true, local_link_available: false,
})

it('waits for a slow periodic read instead of overlapping or starving it', async () => {
  vi.useFakeTimers()
  try {
    await show()
    const slow = deferredResponse()
    fetcher.mockImplementationOnce(() => slow.promise)
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    const calls = fetcher.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(8000))
    expect(fetcher.mock.calls).toHaveLength(calls)
    await act(async () => slow.resolve(snapshot('human')))
    expect(button('Devolver à abelha')).toBeTruthy()
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(fetcher.mock.calls).toHaveLength(calls + 1)
  } finally { vi.useRealTimers() }
})

it.each(['success', 'failure'] as const)('invalidates an old %s read before changing control and suspends polling until the action finishes', async (outcome) => {
  vi.useFakeTimers()
  try {
    await show()
    const old = deferredResponse()
    const control = deferredResponse()
    fetcher.mockImplementationOnce(() => old.promise)
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    fetcher.mockImplementation((url: string) => String(url).endsWith('/control') ? control.promise : Promise.resolve(snapshot('human')))
    await act(async () => button('Assumir controle')!.click())
    const calls = fetcher.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(8000))
    expect(fetcher.mock.calls).toHaveLength(calls)
    await act(async () => control.resolve(Response.json({ ...computer, control_holder: 'human' })))
    expect(button('Devolver à abelha')).toBeTruthy()
    await act(async () => old.resolve(outcome === 'success' ? snapshot() : Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 })))
    expect(button('Devolver à abelha')).toBeTruthy()
    expect(container.querySelector('[role="alert"]')).toBeNull()
    const after = fetcher.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(fetcher.mock.calls).toHaveLength(after + 1)
  } finally { vi.useRealTimers() }
})

it('sends only one control mutation for clicks in the same render', async () => {
  const control = deferredResponse()
  await show()
  fetcher.mockImplementation((url: string) => String(url).endsWith('/control') ? control.promise : Promise.resolve(snapshot('human')))
  await act(async () => { button('Assumir controle')!.click(); button('Assumir controle')!.click() })
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/control'))).toHaveLength(1)
  await act(async () => control.resolve(Response.json({ ...computer, control_holder: 'human' })))
})

it('suspends periodic reads during restart and rejects the snapshot started before it', async () => {
  vi.useFakeTimers()
  try {
    await show()
    const old = deferredResponse()
    fetcher.mockImplementationOnce(() => old.promise)
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    await act(async () => button('Reiniciar')!.click())
    await act(async () => { button('Reiniciar agora')!.click(); button('Reiniciar agora')!.click() })
    expect(restarts()).toHaveLength(1)
    const calls = fetcher.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(8000))
    expect(fetcher.mock.calls).toHaveLength(calls)
    fetcher.mockImplementation(async () => snapshot('human'))
    await act(async () => finish!(Response.json({ ...computer, restarted: true })))
    expect(button('Devolver à abelha')).toBeTruthy()
    await act(async () => old.resolve(snapshot()))
    expect(button('Devolver à abelha')).toBeTruthy()
  } finally { vi.useRealTimers() }
})


it('serializes computer creation and resumes polling after a failed mutation without retrying it', async () => {
  vi.useFakeTimers()
  try {
    const creation = deferredResponse()
    const empty = () => Response.json({ computers: [], runtime_online: true, runtime_paired: true, local_link_available: false })
    fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? creation.promise : empty())
    await show()
    await act(async () => { button('Ligar computador')!.click(); button('Ligar computador')!.click() })
    const posts = () => fetcher.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST')
    expect(posts()).toHaveLength(1)
    const calls = fetcher.mock.calls.length
    await act(async () => vi.advanceTimersByTimeAsync(8000))
    expect(fetcher.mock.calls).toHaveLength(calls)
    await act(async () => creation.resolve(Response.json({ error: { code: 'computer_restart_failed', message: 'x' } }, { status: 409 })))
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(button('Ligar computador')!.disabled).toBe(false)
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(fetcher.mock.calls).toHaveLength(calls + 1)
    expect(posts()).toHaveLength(1)
  } finally { vi.useRealTimers() }
})

it('does not start a new read when a restart finishes after the panel closes', async () => {
  await show()
  await act(async () => button('Reiniciar')!.click())
  await act(async () => button('Reiniciar agora')!.click())
  await act(async () => root.render(null))
  const calls = fetcher.mock.calls.length
  await act(async () => finish!(Response.json({ ...computer, restarted: true })))
  expect(fetcher.mock.calls).toHaveLength(calls)
})

it.each(['initial', 'periodic'] as const)('clears a recovered %s read error without sending a mutation', async (stage) => {
  vi.useFakeTimers()
  try {
    if (stage === 'periodic') await show()
    fetcher.mockImplementationOnce(async () => Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 }))
    if (stage === 'initial') await show()
    else await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    fetcher.mockImplementation(async () => snapshot('human'))
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(button('Devolver à abelha')).toBeTruthy()
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(fetcher.mock.calls.every(([, init]) => (init as RequestInit).method === 'GET')).toBe(true)
  } finally { vi.useRealTimers() }
})

it.each(['creation', 'control', 'restart'] as const)('preserves a failed %s notice when periodic reads recover', async (operation) => {
  vi.useFakeTimers()
  try {
    if (operation === 'creation') fetcher.mockImplementationOnce(async () => Response.json({ computers: [], runtime_online: true, runtime_paired: true, local_link_available: false }))
    await show()
    const failure = () => Response.json({ error: { code: 'computer_restart_failed', message: 'x' } }, { status: 409 })
    fetcher.mockImplementation(async (_url: string, init: RequestInit) => init.method === 'POST' ? failure() : snapshot())
    if (operation === 'restart') {
      await act(async () => button('Reiniciar')!.click())
      await act(async () => button('Reiniciar agora')!.click())
    } else await act(async () => button(operation === 'creation' ? 'Ligar computador' : 'Assumir controle')!.click())
    const alert = container.querySelector('[role="alert"]')?.textContent
    expect(alert).toContain('Não foi possível reiniciar o computador')
    fetcher.mockImplementationOnce(async () => Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 }))
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    await act(async () => vi.advanceTimersByTimeAsync(4000))
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(alert)
    expect(fetcher.mock.calls.filter(([, init]) => (init as RequestInit).method === 'POST')).toHaveLength(1)
  } finally { vi.useRealTimers() }
})
