// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { activationResources } from '../../locales/activation'
import { AgentActivation } from './AgentActivation'
const id = '10000000-0000-4000-8000-000000000001'
const agent: AgentSummary = { id, name: 'Restaurada', purpose: '', instructions: '', provider_config: { kind: 'ollama', endpoint: 'http://127.0.0.1:11434', model: 'qwen3:1.7b', capabilities: { text: true, tool_calls: false } }, revision: 4, status: 'paused', conversation_id: null, memory_enabled: true }
const safety = { snapshot: { event_seq: 1, observed_at: '2026-10-21T20:00:00Z' }, status: 'stopped', generation: 2, revision: 9, changed_at: '2026-10-21T20:00:00Z', reason: 'PRIVATE_REASON', in_flight: { model_calls: 0, tool_actions: 0, chat_reservations: 0, provisioning_effects: 0, executor_commands: 0 } }
const receipt = { agent_id: id, revision: 5, status: 'active', safety_revision: 9, global_status: 'stopped', provider_checked: true }
const onboarding = (value = agent) => ({ agents: [value], vault: { available: true, backend: 'fernet' }, environments: [], next_steps: [] })
let root: Root, container: HTMLDivElement
const onSaved = vi.fn(), onReload = vi.fn(), pt = activationResources['pt-BR']
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.clearAllMocks(); setCsrfToken('csrf'); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })
async function render(lang: keyof typeof activationResources = 'pt-BR', value = agent) {
  const i18n = createInstance(); await i18n.init({ lng: lang, resources: { [lang]: { activation: activationResources[lang] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><AgentActivation key={`${value.id}:${value.revision}`} agent={value} onSaved={onSaved} onReload={onReload} /></I18nextProvider>))
}
const button = (text: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === text)!
async function click(text: string) { await act(async () => button(text).click()) }
async function consent() { await act(async () => container.querySelector('input')!.click()) }
function setup() {
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.endsWith('/onboarding') ? onboarding() : url.endsWith('/safety') ? safety : receipt))); vi.stubGlobal('fetch', fetcher); return fetcher
}
for (const lang of ['pt-BR', 'en', 'es'] as const) it(`uses fresh consent and no automatic reads/mutations in ${lang}`, async () => {
  const fetcher = setup(); await render(lang); expect(fetcher).not.toHaveBeenCalled(); expect(button(activationResources[lang].submit).disabled).toBe(true); expect((container.querySelector('input') as HTMLInputElement).checked).toBe(false); expect(container.textContent).toContain('qwen3:1.7b')
})
it('requires two explicit reads and consent, then only the scoped reactivation POST', async () => {
  const fetcher = setup(); await render(); await click(pt.check); expect(fetcher).toHaveBeenCalledTimes(2); expect(button(pt.submit).disabled).toBe(true); expect(container.textContent).not.toContain('PRIVATE_REASON'); await consent(); await click(pt.submit)
  const writes = fetcher.mock.calls.filter(call => call[1].method === 'POST'); expect(writes).toHaveLength(1); expect(JSON.parse(writes[0][1].body)).toEqual({ expected_revision: 4, expected_safety_revision: 9, consent: 'reactivate_paused_agent' }); expect(onSaved).toHaveBeenCalledOnce()
})
it('blocks activation when stop is running or the saved agent revision changed', async () => {
  for (const mode of ['running', 'revision']) {
    const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(Response.json(url.endsWith('/onboarding') ? onboarding(mode === 'revision' ? { ...agent, revision: 5 } : agent) : { ...safety, status: mode === 'running' ? 'running' : 'stopped' }))); vi.stubGlobal('fetch', fetcher); await render(); await click(pt.check)
    expect(container.textContent).toContain(mode === 'running' ? pt.stopRequired : pt.changed); expect(button(pt.submit).disabled).toBe(true); expect(fetcher.mock.calls.every(call => call[1].method === 'GET')).toBe(true)
    await act(async () => root.unmount()); root = createRoot(container)
  }
})
it('requires reconnection of a missing provider instead of inventing a model', async () => {
  const fetcher = setup(); await render('pt-BR', { ...agent, provider_config: null }); expect(button(pt.check).disabled).toBe(true); expect(fetcher).not.toHaveBeenCalled(); expect(container.textContent).toContain(pt.saveFirst)
})
it('loses confirmation conservatively and reconciles active current state through GET only', async () => {
  const fetcher = setup(); await render(); await click(pt.check); await consent(); fetcher.mockRejectedValueOnce(new Error('PRIVATE_LOST_REPLY')); await click(pt.submit)
  expect(container.textContent).toContain(pt.failure); expect(button(pt.submit).disabled).toBe(true); expect(onSaved).not.toHaveBeenCalled(); expect((container.querySelector('input') as HTMLInputElement).checked).toBe(false)
  fetcher.mockImplementation((url: string) => Promise.resolve(Response.json(url.endsWith('/onboarding') ? onboarding({ ...agent, status: 'active', revision: 5 }) : safety))); await click(pt.check); expect(onSaved).toHaveBeenCalledOnce(); expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1)
})
it('does not treat failed prerequisite lookup as readiness', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('PRIVATE_READ')); vi.stubGlobal('fetch', fetcher); await render(); await click(pt.check); expect(container.textContent).toContain(pt.checkFailure); expect(button(pt.submit).disabled).toBe(true); expect(container.textContent).not.toContain('PRIVATE_READ')
})
it('blocks rapid duplicate submits and discards late confirmation on scope change', async () => {
  const fetcher = setup(); await render(); await click(pt.check); await consent(); let resolve!: (value: Response) => void; fetcher.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await act(async () => { button(pt.submit).click(); button(pt.submit).click() }); const writes = fetcher.mock.calls.filter(call => call[1].method === 'POST'); expect(writes).toHaveLength(1)
  await render('pt-BR', { ...agent, revision: 5 }); expect(writes[0][1].signal.aborted).toBe(true); await act(async () => resolve(Response.json(receipt))); expect(onSaved).not.toHaveBeenCalled(); expect(button(pt.submit).disabled).toBe(true)
})
it('never offers reactivation for active or archived agents', async () => {
  const fetcher = setup(); for (const status of ['active', 'archived'] as const) { await render('pt-BR', { ...agent, status }); expect(container.textContent).toBe('') }
  expect(fetcher).not.toHaveBeenCalled()
})
