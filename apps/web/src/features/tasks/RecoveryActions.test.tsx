// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { recoveryResources } from '../../locales/recovery'
import { taskResources } from '../../locales/tasks'
import type { TaskRecord } from '../../api/tasks'
import { RecoveryActions } from './RecoveryActions'
const agent = '10000000-0000-4000-8000-000000000001', taskId = '20000000-0000-4000-8000-000000000001', run = '30000000-0000-4000-8000-000000000001'
const task = { agent_id: agent, id: taskId, revision: 2 } as TaskRecord
const id = (n: number) => `40000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const page = (start: number, length: number, more = false) => ({ agent_id: agent, task_id: taskId, task_revision: 2, observed_at: '2026-10-21T19:00:00Z', scope: 'all_canonical_task_actions', executor_evidence: 'not_inspected', items: Array.from({ length }, (_, index) => ({ id: id(start + index), run_id: run, revision: 2, tool_name: 'private.tool <script>window.BAD=1</script>', status: 'outcome_unknown', unknown_acknowledged: true, created_at: '2026-10-21T18:00:00Z', updated_at: '2026-10-21T19:00:00Z', executor_command: index === 0 ? 'unknown' : null, executor_code: index === 0 ? 'late_confirmed' : null })), has_more: more, next_cursor: more ? id(start + length - 1) : null })
let root: Root, container: HTMLDivElement
const refreshTask = vi.fn(), pt = recoveryResources['pt-BR']
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.clearAllMocks(); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
async function render(lang: keyof typeof recoveryResources = 'pt-BR', value = task) {
  const i18n = createInstance(); await i18n.init({ lng: lang, resources: { [lang]: { recovery: recoveryResources[lang], tasks: taskResources[lang] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><RecoveryActions key={`${value.agent_id}:${value.id}:${value.revision}`} task={value} onRefresh={refreshTask} /></I18nextProvider>))
}
const button = (text: string) => Array.from(container.querySelectorAll('button')).find(node => node.textContent === text)!
async function click(text: string) { await act(async () => button(text).click()) }
for (const lang of ['pt-BR', 'en', 'es'] as const) it(`does not automatically fetch in ${lang} and explains page snapshots`, async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(lang); expect(fetcher).not.toHaveBeenCalled(); expect(container.textContent).toContain(recoveryResources[lang].historyScope)
})
it('navigates50+50+4 with scoped cursors and explicit previous/first queries', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json(page(1, 50, true))).mockResolvedValueOnce(Response.json(page(51, 50, true))).mockResolvedValueOnce(Response.json(page(101, 4))).mockResolvedValueOnce(Response.json(page(51, 50, true))).mockResolvedValueOnce(Response.json(page(1, 50, true))); vi.stubGlobal('fetch', fetcher); await render(); await click(pt.first)
  expect(container.querySelectorAll('li')).toHaveLength(50); expect(button(pt.previous).disabled).toBe(true); await click(pt.next); await click(pt.next)
  expect(container.querySelectorAll('li')).toHaveLength(4); expect(button(pt.next).disabled).toBe(true); expect(container.textContent).toContain(id(104)); await click(pt.previous); await click(pt.first)
  const urls = fetcher.mock.calls.map(call => call[0]); expect(urls[1]).toContain('after=' + id(50)); expect(urls[2]).toContain('after=' + id(100)); expect(urls[3]).toContain('after=' + id(50)); expect(urls[4]).not.toContain('after=')
})
it('preserves unknown acknowledgement and treats hostile names as inert text without commands', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page(1, 1)))); await render(); await click(pt.first)
  expect(container.textContent).toContain('Resultado desconhecido reconhecido'); expect(container.querySelector('script')).toBeNull(); expect(container.textContent).toContain('<script>window.BAD=1</script>'); expect(container.querySelectorAll('button')).toHaveLength(3); expect(container.textContent).toContain(id(1))
})
it('removes old page on failure and retries the exact requested next cursor', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json(page(1, 50, true))).mockRejectedValueOnce(new Error('PRIVATE')).mockResolvedValueOnce(Response.json(page(51, 1))); vi.stubGlobal('fetch', fetcher); await render(); await click(pt.first); await click(pt.next)
  expect(container.querySelectorAll('li')).toHaveLength(0); expect(container.textContent).toContain(pt.historyError); expect(container.textContent).not.toContain('PRIVATE'); await click(pt.retryPage); expect(fetcher.mock.calls[2][0]).toBe(fetcher.mock.calls[1][0]); expect(container.querySelectorAll('li')).toHaveLength(1)
})
it('never presents revision mismatch as records and exposes task refresh', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...page(1, 1), task_revision: 3 }))); await render(); await click(pt.first); expect(container.querySelectorAll('li')).toHaveLength(0); expect(container.textContent).toContain(pt.stale); await click(pt.refreshTask); expect(refreshTask).toHaveBeenCalledOnce()
})
it('rejects malformed data instead of showing a false empty state', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...page(1, 1), items: null }))); await render(); await click(pt.first); expect(container.textContent).toContain(pt.historyError); expect(container.textContent).not.toContain(pt.emptyPage)
})
it('blocks duplicate requests and discards replies after switching task scope', async () => {
  let resolve!: (value: Response) => void; const fetcher = vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done })); vi.stubGlobal('fetch', fetcher); await render()
  await act(async () => { button(pt.first).click(); button(pt.first).click() }); expect(fetcher).toHaveBeenCalledOnce(); const signal = fetcher.mock.calls[0][1].signal
  await render('pt-BR', { ...task, id: agent }); expect(signal.aborted).toBe(true); await act(async () => resolve(Response.json(page(1, 1)))); expect(container.querySelectorAll('li')).toHaveLength(0)
})
it('shows confirmed empty page only after a valid successful GET', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(page(1, 0))); vi.stubGlobal('fetch', fetcher); await render(); expect(container.textContent).not.toContain(pt.emptyPage); await click(pt.first); expect(container.textContent).toContain(pt.emptyPage); expect(button(pt.next).disabled).toBe(true)
})
it('explains executor queue state and late answers as evidence only', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page(1, 1)))); await render(); await click(pt.first)
  expect(container.textContent).toContain('Não se sabe se o comando foi executado no computador')
  expect(container.textContent).toContain('Uma resposta do computador chegou depois (O computador confirmou o comando.)')
})
