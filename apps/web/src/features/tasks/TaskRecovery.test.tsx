// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { recoveryResources } from '../../locales/recovery'
import type { TaskRecord } from '../../api/tasks'
import { TaskRecovery } from './TaskRecovery'
const agent = '10000000-0000-4000-8000-000000000001', taskId = '20000000-0000-4000-8000-000000000001'
const task = { agent_id: agent, id: taskId, revision: 2 } as TaskRecord
const empty = { prepared: 0, awaiting_approval: 0, ready: 0, dispatch_started: 0, confirmed: 0, failed_no_effect: 0, outcome_unknown: 0, cancelled: 0, unknown_acknowledged: 0, unknown_unacknowledged: 0, total: 0 }
const fixture = (revision = 2) => ({ agent_id: agent, task_id: taskId, task_revision: revision, task_status: 'paused', observed_at: '2026-10-21T19:00:00Z', scope: 'all_canonical_task_history', executor_evidence: 'not_inspected', runs_total: 1003, actions: { ...empty, outcome_unknown: 2, unknown_acknowledged: 1, unknown_unacknowledged: 1, confirmed: 102, total: 104 }, model_calls: { ...empty, prepared: 1002, total: 1002 } })
let root: Root, container: HTMLDivElement
const pt = recoveryResources['pt-BR'], refreshTask = vi.fn()
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
async function render(lang: keyof typeof recoveryResources = 'pt-BR', value = task) {
  const i18n = createInstance(); await i18n.init({ lng: lang, resources: { [lang]: { recovery: recoveryResources[lang] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><TaskRecovery key={`${value.agent_id}:${value.id}:${value.revision}`} task={value} onRefresh={refreshTask} /></I18nextProvider>))
}
async function click() { await act(async () => container.querySelector('button')!.click()) }
for (const lang of ['pt-BR', 'en', 'es'] as const) it(`presents ${lang} scope without automatically fetching or claiming empty history`, async () => {
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(lang)
  expect(fetcher).not.toHaveBeenCalled(); expect(container.querySelector('table')).toBeNull(); expect(container.textContent).toContain(recoveryResources[lang].scope); expect(container.textContent).toContain(recoveryResources[lang].limits)
})
it('shows complete counters, keeps acknowledged unknown separate and has no execution controls', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(fixture()))); await render(); await click()
  expect(container.querySelectorAll('tbody tr')).toHaveLength(9); expect(container.textContent).toContain('Resultados desconhecidos: 2'); expect(container.textContent).toContain('Com risco reconhecido: 1'); expect(container.textContent).toContain(pt.meaning)
  expect(container.querySelectorAll('button')).toHaveLength(1); expect(container.querySelector('button')!.textContent).toBe(pt.refresh)
})
it('discards prior counters on a failed refresh instead of presenting them as current or empty', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json(fixture())).mockRejectedValueOnce(new Error('PRIVATE')); vi.stubGlobal('fetch', fetcher); await render(); await click(); await click()
  expect(container.querySelector('table')).toBeNull(); expect(container.textContent).toContain(pt.failure); expect(container.textContent).not.toContain('PRIVATE'); expect(fetcher).toHaveBeenCalledTimes(2)
})
it('rejects a diagnosis from another task revision and requires refreshing task details', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(fixture(3)))); await render(); await click(); expect(container.querySelector('table')).toBeNull(); expect(container.textContent).toContain(pt.stale); await act(async () => container.querySelectorAll('button')[1].click()); expect(refreshTask).toHaveBeenCalledOnce()
})
it('rejects wrong-scope replies without displaying their counters', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...fixture(), task_id: agent }))); await render(); await click(); expect(container.querySelector('table')).toBeNull(); expect(container.textContent).toContain(pt.failure)
})
it('prevents duplicate clicks and ignores late data after the task revision changes', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi.fn().mockReturnValue(new Promise<Response>(done => { resolve = done })); vi.stubGlobal('fetch', fetcher); await render()
  await act(async () => { container.querySelector('button')!.click(); container.querySelector('button')!.click() }); expect(fetcher).toHaveBeenCalledOnce()
  const signal = fetcher.mock.calls[0][1].signal; await render('pt-BR', { ...task, revision: 3 }); expect(signal.aborted).toBe(true)
  await act(async () => resolve(Response.json(fixture()))); expect(container.querySelector('table')).toBeNull(); expect(container.textContent).not.toContain(pt.failure)
})
it('does not keep a previous task inventory when switching to another task', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(fixture())); vi.stubGlobal('fetch', fetcher); await render(); await click(); await render('pt-BR', { ...task, id: agent })
  expect(container.querySelector('table')).toBeNull(); expect(fetcher).toHaveBeenCalledOnce()
})
