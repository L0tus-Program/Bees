// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { digest } from '../../api/toolTasks'
import { manualRoutineIntent, preserveManualRoutine, restoreManualRoutine } from '../../api/routineManual'
import type { ManualRoutineIntent } from '../../api/routineManual'
import { routineResources } from '../../locales/routines'
import { productResources } from '../../locales/product'
import { ManualRoutineRun } from './ManualRoutineRun'
import { agentId, routine, occurrence } from './fixtures'

let root: Root, container: HTMLDivElement
const created = vi.fn(), close = vi.fn(), lock = vi.fn()
async function reply(intent: ManualRoutineIntent) {
  return { client_request_id: intent.body.client_request_id, request_hash: await digest(intent.body), agent_id: agentId, routine_id: routine.id, routine_revision: 1,
    runtime_binding: { installation_id: agentId, resource_id: routine.id, plugin_id: occurrence.id, manifest_hash: 'a'.repeat(64), tool_version: '1' },
    occurrence: { ...occurrence, status: 'running', reason: 'task_created', task_id: agentId, revision: 2, metadata: { origin: 'manual', task_status: 'queued' } } }
}
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); sessionStorage.clear()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks(); setCsrfToken() })
async function render(language = 'pt-BR', selected = true, available = true) {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: Object.fromEntries(Object.entries(routineResources).map(([lang, routines]) => [lang, { routines, product: productResources[lang as keyof typeof productResources] }])), interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ManualRoutineRun agentId={agentId} routine={selected ? routine : null} available={available} onCreated={created} onClose={close} onLock={lock} /></I18nextProvider>))
}
function button(text: string) { const found = [...container.querySelectorAll('button')].find((b) => b.textContent === text); if (!found) throw new Error('Missing ' + text); return found }
async function until(done: () => boolean) {
  for (let index = 0; index < 250 && !done(); index++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 4)) })
  expect(done()).toBe(true)
}
async function click(text: string) {
  await act(async () => button(text).click())
  await until(() => container.querySelector('section')?.getAttribute('aria-busy') !== 'true')
}
describe('manual routine human confirmation and recovery', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('reviews limits before one write and disables duplicate click in %s', async (language) => {
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => Response.json(await reply({ agentId, routineId: routine.id, body: JSON.parse(options.body as string) })))
    vi.stubGlobal('fetch', fetcher)
    await render(language)
    const words = routineResources[language]
    expect(fetcher).not.toHaveBeenCalled()
    expect(container.textContent).toContain(words.manualEffect)
    expect(document.activeElement).toBe(container.querySelector('h3'))
    await act(async () => { button(words.manualConfirm).click(); button(words.manualConfirm).click() })
    await until(() => created.mock.calls.length === 1)
    expect(fetcher).toHaveBeenCalledTimes(1); expect(created).toHaveBeenCalledWith(agentId)
    expect(restoreManualRoutine(agentId).intent).toBeNull()
  })
  it('reload never resends a committed lost response and receipt lookup opens the same task', async () => {
    let saved: Awaited<ReturnType<typeof reply>> | null = null
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') { saved = await reply({ agentId, routineId: routine.id, body: JSON.parse(options.body as string) }); throw new TypeError('lost after commit') }
      return Response.json(saved)
    })
    vi.stubGlobal('fetch', fetcher)
    await render(); await click(routineResources['pt-BR'].manualConfirm)
    expect(restoreManualRoutine(agentId).intent).not.toBeNull()
    await act(async () => root.unmount()); root = createRoot(container); await render('pt-BR', false)
    expect(fetcher).toHaveBeenCalledTimes(1)
    await click(routineResources['pt-BR'].manualVerify)
    expect(fetcher.mock.calls.map((call) => call[1].method)).toEqual(['POST', 'GET'])
    expect(created).toHaveBeenCalledTimes(1)
  })
  it('404 lookup requires another explicit confirmation to resend the exact same request', async () => {
    const attempts: string[] = []
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'GET') return Response.json({ error: { code: 'not_found' } }, { status: 404 })
      attempts.push(options.body as string)
      if (attempts.length === 1) throw new TypeError('lost before commit')
      return Response.json(await reply({ agentId, routineId: routine.id, body: JSON.parse(options.body as string) }))
    })
    vi.stubGlobal('fetch', fetcher)
    await render(); await click(routineResources['pt-BR'].manualConfirm); await click(routineResources['pt-BR'].manualVerify)
    expect(attempts).toHaveLength(1)
    await click(routineResources['pt-BR'].manualRetry)
    expect(attempts).toHaveLength(2); expect(attempts[0]).toBe(attempts[1])
  })
  it('does not submit from forged storage or permit an unavailable worker', async () => {
    const intent = manualRoutineIntent(routine)
    sessionStorage.setItem(`bees.routine-manual.pending.v1.${agentId}`, JSON.stringify({ ...intent, body: { ...intent.body, approved: true } }))
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await render('pt-BR', false)
    expect(container.textContent).toContain(routineResources['pt-BR'].manualCorrupt)
    await click(routineResources['pt-BR'].manualDiscardRecord)
    await render('pt-BR', true, false)
    expect(button(routineResources['pt-BR'].manualConfirm).disabled).toBe(true)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('keeps an invalid success response uncertain and does not regenerate an identifier', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ accepted: true }))
    vi.stubGlobal('fetch', fetcher)
    await render(); await click(routineResources['pt-BR'].manualConfirm)
    const original = restoreManualRoutine(agentId).intent!
    expect(container.textContent).toContain(routineResources['pt-BR'].manualUncertain)
    expect(created).not.toHaveBeenCalled()
    preserveManualRoutine(original)
    expect(restoreManualRoutine(agentId).intent).toEqual(original)
  })
})
