// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { resourceIntent } from '../../api/resourceTools'
import type { ResourceIntent } from '../../api/resourceTools'
import { productResources } from '../../locales/product'
import { resourceApprovalResources } from '../../locales/resourceApprovals'
import { ResourceApprovalCard } from './ResourceApprovalCard'
import { resourceApproval as source, resourceReceipt } from './resourceFixtures'
import { preserveResourceIntent, restoreResourceIntent } from './resourceRecovery'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
const stored = () => restoreResourceIntent(source.agent_id, source.task_id, source.id)
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); sessionStorage.clear()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { resourceApprovals: resourceApprovalResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render(value = source) { await act(async () => root.render(<I18nextProvider i18n={i18n}><ResourceApprovalCard source={{ ...value, input_preview: null }} disabled={false} onBusy={() => {}} onChanged={() => {}} /></I18nextProvider>)) }
function find(label: string) { return [...container.querySelectorAll('button')].find((item) => item.textContent === label) }
async function click(label: string) { const element = find(label); if (!element) throw new Error(label); await act(async () => element.click()) }
async function settle() { await vi.waitFor(async () => { await act(async () => {}); expect(container.getAttribute('aria-busy') ?? container.querySelector('article')?.getAttribute('aria-busy')).toBe('false') }) }

describe('resource consent and recovery', () => {
  it('requires an explicit read and confirmation, escapes private input, and keeps it out of storage', async () => {
    let intent: ResourceIntent
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method !== 'POST') return Response.json(source)
      const body = JSON.parse(options.body as string)
      intent = { ...resourceIntent(source, 'allow_once'), body }
      return Response.json(resourceReceipt(intent))
    }); vi.stubGlobal('fetch', fetcher)
    await render(); expect(fetcher).not.toHaveBeenCalled(); expect(find('Permitir uma vez')).toBeUndefined()
    await click('Revisar texto e alcance'); await settle()
    expect(container.querySelector('textarea')?.value).toBe(source.input_preview)
    expect(container.querySelector('textarea')?.readOnly).toBe(true); expect(container.querySelector('script')).toBeNull()
    expect(document.activeElement?.tagName).toBe('H5')
    await click('Permitir uma vez'); expect(fetcher).toHaveBeenCalledTimes(1)
    await click('Confirmar decisão'); await settle()
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
    expect(container.textContent).toContain('Decisão registrada pelo serviço')
    expect(stored().intent).toBeNull()
  })
  it.each(['network', 'invalid', 'unauthorized'])('preserves an uncertain %s write and reloads without automatically resending', async (failure) => {
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method !== 'POST') return Response.json(source)
      if (failure === 'network') throw new TypeError('lost')
      return failure === 'invalid' ? Response.json({}) : Response.json({ error: { code: 'session_required' } }, { status: 401 })
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click('Revisar texto e alcance'); await settle(); await click('Permitir uma vez'); await click('Confirmar decisão'); await settle()
    const intent = stored().intent!; expect(intent).not.toBeNull(); expect(JSON.stringify(intent)).not.toContain('approve()')
    expect(find('Concluir conferência sem reenviar')).toBeUndefined()
    await act(async () => root.unmount()); root = createRoot(container); await render()
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(1)
    setCsrfToken('fresh-csrf')
    fetcher.mockImplementation(async (_url, options) => options.method === 'POST' ? Response.json(resourceReceipt(intent)) : Response.json(source))
    await click('Verificar o mesmo pedido'); await settle()
    const posts = fetcher.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(posts).toHaveLength(2); expect(posts[0][1].body).toBe(posts[1][1].body); expect(stored().intent).toBeNull()
  })
  it('requires a fresh successful read before discarding a pending request without sending it', async () => {
    preserveResourceIntent(resourceIntent(source, 'allow_once'))
    const fetcher = vi.fn().mockResolvedValue(Response.json(source)); vi.stubGlobal('fetch', fetcher)
    await render(); expect(find('Concluir conferência sem reenviar')).toBeUndefined()
    await click('Consultar estado atual'); await settle(); await click('Concluir conferência sem reenviar')
    expect(stored().intent).toBeNull(); expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
  })
  it('blocks first send if browser storage cannot retain the exact request', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(source)); vi.stubGlobal('fetch', fetcher)
    await render(); await click('Revisar texto e alcance'); await settle(); await click('Permitir uma vez')
    const storage = sessionStorage
    vi.stubGlobal('sessionStorage', { getItem: (name: string) => storage.getItem(name), removeItem: (name: string) => storage.removeItem(name), clear: () => storage.clear(), setItem: () => { throw new Error('storage unavailable') } })
    await click('Confirmar decisão')
    expect(container.textContent).toContain('Não foi possível preservar'); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('invalidates a selected decision when the latest list changes the grant', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(source)))
    await render(); await click('Revisar texto e alcance'); await settle(); await click('Permitir uma vez')
    await render({ ...source, scope: { ...source.scope, parameters: { ...source.scope.parameters, grant_revision: 2 } } })
    expect(find('Confirmar decisão')).toBeUndefined(); expect(container.querySelector('textarea')).toBeNull()
  })
  it('shows unknown effects before authorization status and never claims the task finished', async () => {
    await render({ ...source, revision: 3, status: 'approved', decision: 'allow_once', consumed_at: source.created_at, valid: false, stale_reason: 'approval_consumed', effect_status: 'outcome_unknown' })
    expect(container.textContent).toContain('O efeito pode ter ocorrido'); expect(find('Permitir uma vez')).toBeUndefined()
    expect(container.textContent).not.toContain('Tarefa concluída')
  })
})
