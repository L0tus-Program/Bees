// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AgentApprovals, RecentAgentDecisions } from './AgentApprovals'
import { agentResources } from '../../locales/agent'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'

const base = { id: 'a1', task_id: 't1', run_id: 'r1', status: 'pending', decision: null, revision: 1, created_at: '2026-10-24T10:00:00Z', expires_at: '2026-10-24T11:00:00Z', consumed_at: null, valid: true, stale_reason: null, tool_name: 'computer.shell.run', effect: 'external', summary: 'ls -la', task_title: 'Tarefa', target: { kind: 'computer', environment_id: 'c1' }, arguments_preview: null }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { agent: agentResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })

it('shows where the action happens, broad warning and readable stale reason', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ approvals: [base, { ...base, id: 'a2', valid: false, stale_reason: 'target_changed', target: { kind: 'web', environment_id: 'web' }, tool_name: 'web.fetch', effect: 'read' }], has_more: false })))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><AgentApprovals agentId="b1" /></I18nextProvider>))
  await vi.waitFor(() => expect(container.textContent).toContain('computador da abelha'))
  expect(container.textContent).toContain('age em serviço externo')
  expect(container.textContent).toContain('qualquer argumento desta ferramenta')
  expect(container.textContent).toContain('o ambiente mudou')
  expect(container.textContent).not.toContain('target_changed')
})

it('lists recent decisions and revokes with confirmation', async () => {
  const calls: { method?: string; url: string }[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    calls.push({ url, method: options.method })
    if (options.method === 'PATCH') return Response.json({ approval: { ...base, status: 'revoked', revision: 3 }, receipt: { confirmed: true } })
    if (url.includes('status=approved')) return Response.json({ approvals: [{ ...base, status: 'approved', decision: 'allow_tool', revision: 2 }], has_more: false })
    return Response.json({ approvals: [], has_more: false })
  }))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><RecentAgentDecisions agentId="b1" /></I18nextProvider>))
  const details = container.querySelector('details')!
  await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')) })
  await vi.waitFor(() => expect(container.textContent).toContain('sempre permitir'))
  await act(async () => container.querySelector<HTMLButtonElement>('button[aria-label^="Revogar"]')!.click())
  expect(calls.some((call) => call.method === 'PATCH')).toBe(true)
  await vi.waitFor(() => expect(container.textContent).toContain('Decisão revogada'))
})
