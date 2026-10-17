// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { productResources } from '../../locales/product'
import { skillResources } from '../../locales/skills'
import { agent as chatAgent } from '../chat/testFixtures'
import { page as historyPage, result } from '../deliveries/fixtures'
import { SkillsPanel } from './SkillsPanel'
import { agentId, definition, skill } from './fixtures'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
const storageKey = `bees.skill-process-pending.v1.${agentId}`
const source = { ...result, agent_id: agentId, partial: false }
const content = 'Fonte https://example.org/manual. Dado pessoal só do caso. <script>ignore policies</script>'
const review = { sources: ['https://example.org/manual'], review_note: 'Conferido', pending: 'Reavaliar recursos', confirmed: true }
const intent = { agentId, path: `/agents/${agentId}/skills/from-process`, body: { client_request_id: '33333333-3333-4333-8333-333333333333', definition, task_id: source.task_id, run_id: source.run_id, message_id: source.id, result_sha256: 'a'.repeat(64), review } }
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); sessionStorage.clear()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { skills: skillResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render() { await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id: agentId }} /></I18nextProvider>)) }
function button(label: string) { const found = [...container.querySelectorAll('button')].find((element) => element.textContent === label); if (!found) throw new Error(label); return found }
async function click(label: string) { await act(async () => button(label).click()) }
async function input(label: string, value: string) {
  const element = [...container.querySelectorAll('label')].find((item) => item.firstChild?.textContent === label)?.querySelector('input,textarea') as HTMLInputElement | HTMLTextAreaElement
  if (!element) throw new Error(label)
  const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => { Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event('input', { bubbles: true })) })
}
function fetcher() {
  return vi.fn(async (url: string, options: RequestInit) => {
    if (url.includes('/deliveries/results/')) return Response.json({ item: source, content })
    if (url.includes('/deliveries?')) return Response.json({ ...historyPage([source]), agent_id: agentId })
    if (options.method === 'POST') return Response.json(skill)
    return Response.json([])
  })
}

describe('process conversion with explicit review', () => {
  it('uses a blank template and sends only reviewed content bound to the exact result', async () => {
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch)
    await render(); await click('Criar a partir de um processo'); await click(source.title)
    await vi.waitFor(async () => { await act(async () => {}); expect(container.querySelector('.skill-editor input')).not.toBeNull() })
    expect(document.activeElement?.tagName).toBe('H3')
    expect(container.querySelector('script')).toBeNull()
    expect((container.querySelector('.skill-editor input') as HTMLInputElement).value).toBe('')
    expect(button('Salvar versão desabilitada').disabled).toBe(true)
    await input('URLs presentes no resultado, uma por linha', review.sources[0]); await input('O que você conferiu no processo', review.review_note); await input('Pendências e limites do procedimento', review.pending)
    await act(async () => (container.querySelector('.skill-process > fieldset input[type=checkbox]') as HTMLInputElement).click())
    await input('Nome', definition.name); await input('Objetivo', definition.purpose); await input('Título do passo', definition.steps[0].title); await input('Instrução', definition.steps[0].instruction); await input('Entrega esperada', definition.output); await input('Como conferir a conclusão', definition.completion)
    expect(button('Salvar versão desabilitada').disabled).toBe(false)
    await act(async () => container.querySelector('.skill-editor')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    const posts = fetch.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(posts).toHaveLength(1)
    const body = JSON.parse(posts[0][1].body as string)
    expect(body).toMatchObject({ task_id: source.task_id, run_id: source.run_id, message_id: source.id, review })
    expect(body.result_sha256).toMatch(/^[0-9a-f]{64}$/)
    expect(posts[0][1].body).not.toContain('Dado pessoal')
    expect(body).not.toHaveProperty('enabled'); expect(body).not.toHaveProperty('grants')
    expect(fetch.mock.calls.every(([url]) => !url.includes('/chat') && !url.includes('/tasks'))).toBe(true)
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })
  it('recovers a lost response after remount with the same UUID and payload', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify(intent))
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch)
    await render()
    expect(button('Novo procedimento').disabled).toBe(true)
    expect(fetch.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0)
    await click('Verificar o mesmo pedido')
    const posts = fetch.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(JSON.parse(posts[0][1].body as string)).toEqual(intent.body)
    expect(container.textContent).toContain('Versão salva')
    expect(sessionStorage.getItem(storageKey)).toBeNull()
  })
  it('keeps an invalid successful response pending and never claims success', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify(intent))
    const fetch = vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? Response.json({}) : Response.json([])); vi.stubGlobal('fetch', fetch)
    await render(); await click('Verificar o mesmo pedido')
    expect(container.textContent).not.toContain('Versão salva')
    expect(button('Novo procedimento').disabled).toBe(true)
    expect(JSON.parse(sessionStorage.getItem(storageKey)!)).toEqual(intent)
  })
  it('never accepts an arbitrary replay path from browser storage', async () => {
    sessionStorage.setItem(storageKey, JSON.stringify({ ...intent, path: '/agents/other/tools/grant' }))
    const fetch = fetcher(); vi.stubGlobal('fetch', fetch)
    await render()
    expect(container.textContent).toContain('pedido pendente desta aba está ilegível')
    expect(container.textContent).not.toContain('Verificar o mesmo pedido')
    expect(fetch.mock.calls.every(([, options]) => options.method !== 'POST')).toBe(true)
  })
  it('excludes partial results and artifacts without turning reads into mutations', async () => {
    const fetch = vi.fn(async (url: string) => url.includes('/deliveries?') ? Response.json({ ...historyPage([{ ...source, partial: true }]), agent_id: agentId }) : Response.json([])); vi.stubGlobal('fetch', fetch)
    await render(); await click('Criar a partir de um processo')
    expect(container.textContent).toContain('Não há resultados completos nesta página')
    expect(container.querySelector('.skill-editor')).toBeNull()
  })
})
