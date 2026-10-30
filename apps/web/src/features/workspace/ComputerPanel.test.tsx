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
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)) })
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
