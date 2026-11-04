// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SkillRunner } from './SkillRunner'
import { runnableSkill, skillTaskIntent, skillTaskReceipt } from './skillTaskFixtures'
import { skillResources } from '../../locales/skills'
import { productResources } from '../../locales/product'
import { agent } from '../chat/testFixtures'
import { toolTaskCapabilities } from '../tasks/toolTaskFixtures'
import { setCsrfToken } from '../../api/client'
import { preserveSkillTask } from '../../api/skillTasks'
import type { SkillTaskBody } from '../../api/skillTasks'

vi.mock('../tasks/TaskDetail', () => ({ TaskDetail: ({ taskId }: { taskId: string }) => <div data-testid="created-task">{taskId}</div> }))
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
const selectedAgent = { ...agent, id: skillTaskIntent.agentId }
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); sessionStorage.clear(); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { skills: skillResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })
async function render(selected: typeof runnableSkill | null = runnableSkill) { await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillRunner agent={selectedAgent} selected={selected} onClose={() => {}} onLock={() => {}} /></I18nextProvider>)) }
async function submit() {
  const input = container.querySelector('textarea')!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Olá'); input.dispatchEvent(new Event('input', { bubbles: true })) })
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
}
function button(text: string) { return [...container.querySelectorAll('button')].find((item) => item.textContent === text)! }
it('shows fixed procedure and inputs before any write, then confirms once', async () => {
  const writes: SkillTaskBody[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { const body = JSON.parse(options.body as string); writes.push(body); return Response.json(skillTaskReceipt(body)) }
    return Response.json(toolTaskCapabilities)
  }))
  await render()
  expect(writes).toHaveLength(0); expect(container.textContent).toContain('Versão 1'); expect(container.textContent).toContain('Registrar fonte e data')
  await submit()
  expect(writes).toHaveLength(1); expect(writes[0].inputs).toEqual({ texto: 'Olá' }); await vi.waitFor(() => expect(container.querySelector('[data-testid=created-task]')).not.toBeNull())
})
it('preserves a committed lost response across reload and only verifies on explicit click', async () => {
  const writes: SkillTaskBody[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { const body = JSON.parse(options.body as string); writes.push(body); if (writes.length === 1) throw new TypeError('Lost after commit'); return Response.json(skillTaskReceipt(body)) }
    return Response.json(toolTaskCapabilities)
  }))
  await render(); await submit(); expect(writes).toHaveLength(1)
  await act(async () => root.unmount()); root = createRoot(container); await render(null)
  expect(writes).toHaveLength(1); expect(container.textContent).toContain('Olá')
  await act(async () => button('Verificar o mesmo pedido').click())
  expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]); await vi.waitFor(() => expect(sessionStorage.length).toBe(0))
})
it('never writes when tab storage fails', async () => {
  const memory = new Map<string, string>()
  vi.stubGlobal('sessionStorage', { getItem: (key: string) => memory.get(key) ?? null, setItem: () => { throw new Error('Storage unavailable') }, removeItem: (key: string) => memory.delete(key), clear: () => memory.clear() })
  const fetcher = vi.fn(async () => Response.json(toolTaskCapabilities)); vi.stubGlobal('fetch', fetcher)
  await render(); await submit()
  expect(container.textContent).toContain(skillResources['pt-BR'].processStorage); expect(fetcher.mock.calls).toHaveLength(1)
})
it('blocks unreadable recovery without automatic POST', async () => {
  preserveSkillTask(skillTaskIntent)
  sessionStorage.setItem(`bees.skill-task.pending.v1.${skillTaskIntent.agentId}`, '{}')
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  await render(null)
  expect(container.textContent).toContain(skillResources['pt-BR'].processRecoveryBlocked); expect(fetcher).not.toHaveBeenCalled()
})

it.each(['pt-BR', 'en', 'es'] as const)('keeps recovery for an incompatible receipt and opens only the confirmed task (%s)', async (language) => {
  for (const lng of ['en', 'es'] as const) {
    i18n.addResourceBundle(lng, 'skills', skillResources[lng])
    i18n.addResourceBundle(lng, 'product', productResources[lng])
  }
  await i18n.changeLanguage(language)
  const writes: SkillTaskBody[] = []
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body as string); writes.push(body)
      const receipt = skillTaskReceipt(body)
      if (writes.length === 1) receipt.request_hash = 'b'.repeat(64)
      return Response.json(receipt)
    }
    return Response.json(toolTaskCapabilities)
  })
  vi.stubGlobal('fetch', fetcher)
  await render(); await submit()
  expect(writes).toHaveLength(1)
  expect(container.querySelector('[data-testid=created-task]')).toBeNull()
  expect(sessionStorage.getItem(`bees.skill-task.pending.v1.${selectedAgent.id}`)).toContain(writes[0].client_request_id)
  expect(container.textContent).toContain(skillResources[language].runPending)
  await act(async () => root.unmount()); root = createRoot(container); await render(null)
  expect(writes).toHaveLength(1)
  await act(async () => button(skillResources[language].verify).click())
  expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0])
  await vi.waitFor(() => expect(container.querySelector('[data-testid=created-task]')?.textContent).toBe(skillTaskReceipt().task.id))
  expect(sessionStorage.getItem(`bees.skill-task.pending.v1.${selectedAgent.id}`)).toBeNull()
})
