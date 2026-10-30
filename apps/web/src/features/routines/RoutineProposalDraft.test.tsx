// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { routineResources } from '../../locales/routines'
import { productResources } from '../../locales/product'
import { RoutineProposalDraft } from './RoutineProposalDraft'
import { agentId, conversationId, occurrence, routine, skill, definition } from './fixtures'

const source = { conversation_id: conversationId, message_id: occurrence.id, content_hash: 'a'.repeat(64) }
const proposal = { source, proposal: { name: definition.name, instructions: definition.instructions, schedule: definition.schedule, timezone: definition.timezone, inputs: definition.inputs, notification: definition.notification, grace_seconds: definition.grace_seconds } }
let root: Root, container: HTMLDivElement
const close = vi.fn(), lock = vi.fn()
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); sessionStorage.clear(); setCsrfToken('csrf'); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks(); setCsrfToken() })
async function render(language: keyof typeof routineResources = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { routines: routineResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><RoutineProposalDraft agentId={agentId} conversationId={conversationId} messageId={occurrence.id} onClose={close} onLock={lock} /></I18nextProvider>))
}
function button(name: string) { const found = [...container.querySelectorAll('button')].find((b) => b.textContent === name); if (!found) throw new Error('Missing ' + name); return found }
async function click(name: string) { await act(async () => button(name).click()) }
async function choose() {
  const select = container.querySelector('select')!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select, skill.id); select.dispatchEvent(new Event('change', { bubbles: true })) })
}
async function review() { await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) }) }
function setup(lost = false) {
  const writes: string[] = [], reads: string[] = []
  let saved = routine
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'GET') {
      reads.push(url)
      if (url.includes('/proposals/')) return Response.json(proposal)
      if (url.includes('/skills')) return Response.json([skill])
      return Response.json([saved])
    }
    writes.push(options.body as string)
    const body = JSON.parse(options.body as string)
    saved = { ...routine, name: body.definition.name, instructions: body.definition.instructions, schedule: body.definition.schedule, timezone: body.definition.timezone, definition: body.definition, metadata: { proposal_source: body.proposal_source } }
    if (lost && writes.length === 1) throw new TypeError('lost after commit')
    return Response.json(saved)
  })
  vi.stubGlobal('fetch', fetcher)
  return { fetcher, writes, reads }
}
describe('human routine proposal draft', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('requires a human procedure and final confirmation in %s', async (language) => {
    const { writes } = setup(); await render(language)
    expect(container.querySelector('.routine-editor')).toBeNull(); expect(writes).toHaveLength(0)
    expect((container.querySelector('select') as HTMLSelectElement).value).toBe('')
    await choose(); expect((container.querySelector('input[type=checkbox]') as HTMLInputElement).checked).toBe(false)
    await review(); expect(writes).toHaveLength(0)
    await click(routineResources[language].confirm)
    const sent = JSON.parse(writes[0]); expect(sent.proposal_source).toEqual(source); expect(sent).not.toHaveProperty('active')
    expect(sent.definition.execution_mode).toBe('prepare'); expect(sent.definition.runtime_binding).toBeNull()
    expect(container.textContent).toContain(routineResources[language].proposalSaved)
    expect(writes).toHaveLength(1)
  })
  it('preserves the exact creation across lost response/reload and reads before retry', async () => {
    const { writes, reads } = setup(true)
    await render(); await choose(); await review(); await click(routineResources['pt-BR'].confirm)
    expect(writes).toHaveLength(1)
    await act(async () => root.unmount()); root = createRoot(container); const before = reads.length
    await render(); expect(writes).toHaveLength(1); expect(reads).toHaveLength(before)
    expect(container.textContent).toContain(routineResources['pt-BR'].lost)
    await click(routineResources['pt-BR'].verify)
    expect(reads.at(-1)).toContain('/routines?'); expect(writes).toHaveLength(2); expect(writes[0]).toBe(writes[1])
    expect(container.textContent).toContain(routineResources['pt-BR'].proposalSaved)
  })
  it('blocks forged recovery without sending any stored payload', async () => {
    sessionStorage.setItem(`bees.routine-pending.v1.${agentId}`, JSON.stringify({ agentId, action: 'create', body: { client_request_id: crypto.randomUUID(), definition, active: true, proposal_source: source } }))
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render()
    expect(container.textContent).toContain(routineResources['pt-BR'].proposalOtherPending)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('invalid success remains uncertain and never starts a second request automatically', async () => {
    const { writes, fetcher } = setup()
    fetcher.mockImplementation(async (url: string, options: RequestInit) => {
      if (options.method === 'GET') return Response.json(url.includes('/skills') ? [skill] : proposal)
      writes.push(options.body as string); return Response.json({ accepted: true })
    })
    await render(); await choose(); await review(); await click(routineResources['pt-BR'].confirm)
    expect(writes).toHaveLength(1); expect(container.textContent).toContain(routineResources['pt-BR'].lost)
    expect(sessionStorage.getItem(`bees.routine-pending.v1.${agentId}`)).toContain(JSON.parse(writes[0]).client_request_id)
  })
})
