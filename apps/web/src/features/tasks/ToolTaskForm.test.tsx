// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { taskResources } from '../../locales/tasks'
import { toolTaskResources } from '../../locales/toolTasks'
import { productResources } from '../../locales/product'
import { agent } from '../chat/testFixtures'
import { TaskForm } from './TaskForm'
import { preserveToolTask, restoreToolTask } from './toolTaskRecovery'
import { toolTaskCapabilities, toolTaskIntent as intent, toolTaskReceipt } from './toolTaskFixtures'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
const created = vi.fn()
const subject = { ...agent, id: intent.agentId }
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); sessionStorage.clear(); created.mockReset()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { tasks: taskResources['pt-BR'], toolTasks: toolTaskResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render() { await act(async () => root.render(<I18nextProvider i18n={i18n}><TaskForm agent={subject} onCreated={created} onClose={() => {}} onRefresh={() => {}} /></I18nextProvider>)) }
function button(text: string) { const result = [...container.querySelectorAll('button')].find((item) => item.textContent === text); if (!result) throw new Error(text); return result }
async function click(text: string) { await act(async () => button(text).click()) }
async function fill(selector: string, value: string) {
  const input = container.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement
  const prototype = input instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype
  await act(async () => { Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}
function fetcher() { return vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? Response.json(toolTaskReceipt(JSON.parse(options.body as string))) : Response.json(toolTaskCapabilities)) }
async function prepare() {
  await render(); await vi.waitFor(async () => { await act(async () => {}); expect(container.querySelector('[aria-describedby=task-tools-help]')).not.toBeNull() })
  expect((container.querySelector('[aria-describedby=task-tools-help]') as HTMLInputElement).checked).toBe(false)
  await fill('#task-title', intent.body.title); await fill('#task-objective', intent.body.objective); await fill('#task-expected', intent.body.expected_result)
  await act(async () => (container.querySelector('[aria-describedby=task-tools-help]') as HTMLInputElement).click())
}
async function submit() { await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))) }
async function settled() { await vi.waitFor(async () => { await act(async () => {}); expect(button('Verificar o mesmo pedido').disabled).toBe(false) }) }

describe('opt-in local text task', () => {
  it('starts unchecked, names limits and consequences, then confirms the exact creation receipt', async () => {
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch)
    await prepare(); expect(container.textContent).toContain('Até 4 gerações, 16 operações e 120 segundos ativos')
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0)
    await submit(); await vi.waitFor(() => expect(created).toHaveBeenCalledOnce())
    const writes = fetch.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(writes).toHaveLength(1); expect(writes[0][0]).toContain('/tool-tasks')
    const body = JSON.parse(writes[0][1].body as string)
    expect(body).toMatchObject({ max_calls: 4, max_active_seconds: 120, deliverables: [] }); expect(body).not.toHaveProperty('resource_id'); expect(body).not.toHaveProperty('conversation_id')
    expect(restoreToolTask(intent.agentId).intent).toBeNull()
  })
  it.each(['network', 'invalid', 'unauthorized'])('keeps a %s creation pending across remount and checks the same request explicitly', async (failure) => {
    const fetch = fetcher(); fetch.mockImplementation(async (_url, options) => {
      if (options.method !== 'POST') return Response.json(toolTaskCapabilities)
      if (failure === 'network') throw new TypeError('lost')
      return failure === 'invalid' ? Response.json({}) : Response.json({ error: { code: 'session_required' } }, { status: 401 })
    }); vi.stubGlobal('fetch', fetch)
    await prepare(); await submit(); await settled()
    const pending = restoreToolTask(intent.agentId).intent!; expect(pending).not.toBeNull(); expect(created).not.toHaveBeenCalled()
    await act(async () => root.unmount()); root = createRoot(container); await render()
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
    setCsrfToken('fresh-csrf'); fetch.mockImplementation(async (_url, options) => options.method === 'POST' ? Response.json(toolTaskReceipt(pending.body)) : Response.json(toolTaskCapabilities))
    await click('Verificar o mesmo pedido'); await vi.waitFor(() => expect(created).toHaveBeenCalledOnce())
    const writes = fetch.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(writes).toHaveLength(2); expect(writes[0][1].body).toBe(writes[1][1].body)
    expect(restoreToolTask(intent.agentId).intent).toBeNull()
  })
  it('does not create if tab storage cannot retain the command first', async () => {
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch); await prepare()
    const storage = sessionStorage
    vi.stubGlobal('sessionStorage', { getItem: (name: string) => storage.getItem(name), removeItem: (name: string) => storage.removeItem(name), clear: () => storage.clear(), setItem: () => { throw new Error('unavailable') } })
    await submit(); expect(container.textContent).toContain('Nenhuma nova criação foi enviada')
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0)
  })
  it('never replays arbitrary browser storage paths or authority', async () => {
    preserveToolTask(intent)
    const name = `bees.tool-task.pending.v1.${intent.agentId}`
    sessionStorage.setItem(name, JSON.stringify({ ...intent, path: '/tools/execute' }))
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch); await render()
    expect(container.textContent).toContain('Não foi possível ler o pedido preservado')
    expect(container.querySelector('form')).toBeNull(); expect(created).not.toHaveBeenCalled()
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0)
  })
})
