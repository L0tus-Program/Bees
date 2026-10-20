// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { attentionKinds } from '../../api/attention'
import { attentionResources } from '../../locales/attention'
import { productResources } from '../../locales/product'
import { agent as original } from '../chat/testFixtures'
import { AttentionPanel } from './AttentionPanel'

vi.mock('../tasks/TaskDetail', () => ({ TaskDetail: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>Close task detail</button> }))
const agent = { ...original, id: '10000000-0000-4000-8000-000000000001' }
const a = '40000000-0000-4000-8000-000000000001', b = '40000000-0000-4000-8000-000000000002'
const stamp = '2026-10-20T17:00:00Z'
const item = { id: '20000000-0000-4000-8000-000000000001', agent_id: agent.id, task_id: '30000000-0000-4000-8000-000000000001', task_title: 'Aviso A', approval_id: null, kind: 'task_failed', created_at: stamp, revision: 1, read: false, requires_attention: true, muted: false }
const itemB = { ...item, id: b, task_id: '30000000-0000-4000-8000-000000000002', task_title: 'Aviso B' }
const conversations = [{ id: a, title: 'Conversa <script>', created_at: stamp }, { id: b, title: 'Conversa B', created_at: stamp }]
const prefs = attentionKinds.map(kind => ({ kind, enabled: true, revision: 0 }))
let root: Root, container: HTMLDivElement
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setCsrfToken() })
async function render(language: keyof typeof attentionResources = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { attention: attentionResources[language], product: productResources[language] } } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><AttentionPanel agent={agent} /></I18nextProvider>))
}
async function choose(id: string) { await act(async () => { container.querySelector('select')!.value = id; container.querySelector('select')!.dispatchEvent(new Event('change', { bubbles: true })) }) }
function button(name: string) { const found = [...container.querySelectorAll('button')].find(x => x.textContent === name); if (!found) throw new Error('Missing '+name); return found }
async function click(name: string) { await act(async () => button(name).click()) }
function setup({ lost = false, deferA = false, failB = false, catalog = conversations } = {}) {
  const writes: { url: string; body: string }[] = [], reads: string[] = []; let saved = item, pendingA: ((response: Response) => void) | undefined, catalogPage = 0
  const fetcher = vi.fn(async (input: string, options: RequestInit) => {
    const url = new URL(input, 'http://fixture.invalid')
    if (options.method === 'PUT') {
      writes.push({ url: input, body: options.body as string }); saved = { ...item, revision: 2, read: true }
      if (lost && writes.length === 1) throw new TypeError('response lost after commit')
      return Response.json(saved)
    }
    reads.push(input)
    if (url.pathname.endsWith('/conversations')) { catalogPage++; return Response.json({ agent_id: agent.id, items: catalogPage === 1 ? catalog : [catalog[0], { ...conversations[1], id: b }], next_offset: catalog.length === 50 && catalogPage === 1 ? 50 : null }) }
    if (url.pathname.endsWith('/preferences')) return Response.json({ agent_id: agent.id, items: prefs })
    const scope = url.searchParams.get('conversation_id')
    if (scope === a && deferA) return new Promise<Response>(resolve => { pendingA = resolve })
    if (scope === b && failB) { failB = false; return Response.json({ code: 'service_unavailable', message: 'Unavailable' }, { status: 503 }) }
    const items = scope === a ? [saved] : scope === b ? [itemB] : [saved, itemB]
    return Response.json({ ...(scope ? { conversation_id: scope } : {}), items, next_before: null, unread_count: items.filter(x => !x.read).length })
  })
  vi.stubGlobal('fetch', fetcher)
  return { writes, reads, resolveA: () => pendingA?.(Response.json({ conversation_id: a, items: [item], next_before: null, unread_count: 1 })) }
}
describe('attention scope workspace', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('offers an owned human-readable scope in %s without writes', async (language) => {
    const { writes } = setup(); await render(language)
    expect(container.textContent).toContain(attentionResources[language].scope)
    expect(container.querySelector('script')).toBeNull()
    await choose(a); expect(container.querySelector('.attention-list')!.textContent).toContain('Aviso A'); expect(container.querySelector('.attention-list')!.textContent).not.toContain('Aviso B')
    expect(container.textContent).toContain(attentionResources[language].preferenceScope)
    await choose(b); expect(container.querySelector('.attention-list')!.textContent).toContain('Aviso B'); expect(writes).toHaveLength(0)
  })
  it('aborts the old scope and never merges a late page into the new conversation', async () => {
    const state = setup({ deferA: true }); await render(); await choose(a); await choose(b)
    await act(async () => state.resolveA())
    expect(container.querySelector('.attention-list')!.textContent).toContain('Aviso B')
    expect(container.querySelector('.attention-list')!.textContent).not.toContain('Aviso A')
    expect(state.writes).toHaveLength(0)
  })
  it('keeps the selected scope on failure and never presents the previous list or a false empty state', async () => {
    setup({ failB: true }); await render(); await choose(b)
    expect(container.querySelector('select')!.value).toBe(b); expect(container.querySelector('.attention-list')).toBeNull()
    expect(container.textContent).not.toContain(attentionResources['pt-BR'].empty)
    await click(attentionResources['pt-BR'].refresh)
    expect(container.querySelector('.attention-list')!.textContent).toContain('Aviso B')
  })
  it('locks scope after an uncertain read and retries the same body in the same conversation', async () => {
    const state = setup({ lost: true }); await render(); await choose(a); await click(attentionResources['pt-BR'].markRead)
    expect(container.querySelector('select')!.disabled).toBe(true)
    expect(container.textContent).toContain(attentionResources['pt-BR'].lost)
    await click(attentionResources['pt-BR'].retry)
    expect(state.writes).toHaveLength(2); expect(state.writes[0]).toEqual(state.writes[1]); expect(state.writes[0].url).toContain('conversation_id='+a)
    expect(container.querySelector('select')!.disabled).toBe(false)
  })
  it('keeps scope locked while task detail can contain a pending human command', async () => {
    setup(); await render(); await choose(a); await click(attentionResources['pt-BR'].openTask)
    expect(container.querySelector('select')!.disabled).toBe(true)
    await click('Close task detail'); expect(container.querySelector('select')!.disabled).toBe(false)
  })
  it('loads older conversation metadata explicitly and deduplicates shifted catalog pages', async () => {
    const catalog = Array.from({ length: 50 }, (_, i) => ({ id: `${i.toString(16).padStart(8, '0')}-0000-4000-8000-000000000001`, title: 'Older '+i, created_at: stamp }))
    const state = setup({ catalog }); await render(); expect(container.querySelectorAll('option')).toHaveLength(51)
    await click(attentionResources['pt-BR'].moreConversations); expect(container.querySelectorAll('option')).toHaveLength(52)
    expect(state.reads.some(url => url.endsWith('offset=50'))).toBe(true); expect(state.writes).toHaveLength(0)
  })
})
