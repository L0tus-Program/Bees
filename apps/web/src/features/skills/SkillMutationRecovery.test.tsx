// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { enableIntent, importIntent, skillIntent } from '../../api/skills'
import { preserveSkillMutation } from '../../api/skillRecovery'
import { agent as chatAgent } from '../chat/testFixtures'
import { skillResources } from '../../locales/skills'
import { productResources } from '../../locales/product'
import { SkillsPanel } from './SkillsPanel'
import { agentId, definition, skill } from './fixtures'

let root: Root
let container: HTMLDivElement
const key = `bees.skill-library.pending.v1.${agentId}`
const other = '33333333-3333-4333-8333-333333333333'
beforeEach(() => {
  sessionStorage.clear(); vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
function button(text: string) {
  const value = [...container.querySelectorAll('button')].find((item) => item.textContent === text)
  if (!value) throw new Error('Missing ' + text)
  return value
}
async function settled() { await vi.waitFor(async () => { await act(async () => {}); expect(container.querySelector('[aria-busy=true]')).toBeNull() }) }
async function click(text: string) { await act(async () => button(text).click()); await settled() }
async function mount(language: 'pt-BR' | 'en' | 'es', id = agentId) {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { skills: skillResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id }} /></I18nextProvider>)); await settled()
}
async function reload(language: 'pt-BR' | 'en' | 'es') { await act(async () => root.unmount()); root = createRoot(container); await mount(language) }
async function importFile(language: 'pt-BR' | 'en' | 'es') {
  const input = container.querySelector('input[type=file]') as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: [new File([JSON.stringify({ format: 'bees.skill/v1', scope: 'agent', definition })], 'procedure.json')] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await click(skillResources[language].save)
}
const intents = () => [skillIntent(agentId, definition), skillIntent(agentId, definition, skill), importIntent(agentId, { format: 'bees.skill/v1', scope: 'agent', definition }), enableIntent(skill), enableIntent({ ...skill, enabled_version: 1 })]
function receipt(index: number) {
  if (index >= 3) return { ...skill, revision: 2, enabled_version: index === 3 ? 1 : null }
  if (index === 1) return { ...skill, revision: 2, current_version: 2, current: { ...skill.current, version: 2 } }
  return index === 2 ? { ...skill, current: { ...skill.current, origin: 'import' } } : skill
}

describe.each(['pt-BR', 'en', 'es'] as const)('%s durable library request', (language) => {
  const strings = skillResources[language]
  it.each([0, 1, 2, 3, 4])('restores operation %s and requires human verification of its exact UUID/body/endpoint', async (index) => {
    const intent = preserveSkillMutation(intents()[index])
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => Response.json(options.method === 'GET' ? [skill] : receipt(index)))
    vi.stubGlobal('fetch', fetcher)
    await mount(language)
    expect(container.textContent).toContain(strings.lost)
    if (index < 3) expect(container.querySelector('.transfer-notice')?.textContent).toContain(definition.steps[0].instruction)
    else expect(container.querySelector('.transfer-notice')?.textContent).toContain(strings.version.replace('{{version}}', '1'))
    expect(button(strings.create).disabled).toBe(true)
    await click(strings.refresh)
    await reload(language)
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
    await click(strings.verify)
    const writes = fetcher.mock.calls.filter(([, options]) => options.method !== 'GET')
    expect(writes).toHaveLength(1)
    expect(writes[0][0]).toBe('/api/v1' + intent.path)
    expect(writes[0][1].method).toBe(intent.method ?? 'POST')
    expect(writes[0][1].body).toBe(JSON.stringify(intent.body))
    expect(sessionStorage.getItem(key)).toBeNull()
    expect(container.textContent).toContain(strings.saved)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/chat'))).toBe(true)
  })
  it('persists before dispatch, survives lost response and does not forget it on later 403', async () => {
    let attempts = 0
    const bodies: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'GET') return Response.json([])
      expect(sessionStorage.getItem(key)).not.toBeNull()
      expect(JSON.parse(sessionStorage.getItem(key)!).body).toEqual(JSON.parse(options.body as string))
      bodies.push(options.body as string); attempts++
      if (attempts === 1) throw new TypeError('Lost')
      if (attempts === 2) return Response.json({ error: { code: 'forbidden' } }, { status: 403 })
      return Response.json(receipt(2))
    }))
    await mount(language); await importFile(language)
    expect(attempts).toBe(1)
    await reload(language)
    expect(attempts).toBe(1)
    await click(strings.verify)
    expect(sessionStorage.getItem(key)).not.toBeNull()
    expect(button(strings.create).disabled).toBe(true)
    await reload(language); await click(strings.verify)
    expect(bodies).toHaveLength(3)
    expect(new Set(bodies).size).toBe(1)
    expect(sessionStorage.getItem(key)).toBeNull()
  })
  it.each(['corrupt', 'foreign', 'read'])('blocks %s recovery without POST/PUT', async (failure) => {
    sessionStorage.setItem(key, failure === 'foreign' ? JSON.stringify({ ...intents()[0], agentId: other }) : '{')
    if (failure === 'read') vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('private diagnostic') } })
    const fetcher = vi.fn(async () => Response.json([skill])); vi.stubGlobal('fetch', fetcher)
    await mount(language)
    expect(container.textContent).toContain(strings.processRecoveryBlocked)
    expect(container.textContent).not.toContain('private diagnostic')
    expect(button(strings.create).disabled).toBe(true)
    expect(button(strings.enable).disabled).toBe(true)
    expect(fetcher.mock.calls).toHaveLength(1)
  })
  it('refuses to dispatch when preserving the command fails', async () => {
    const fetcher = vi.fn(async () => Response.json([])); vi.stubGlobal('fetch', fetcher)
    await mount(language)
    vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: () => { throw new Error('Quota') } })
    await importFile(language)
    expect(container.textContent).toContain(strings.processStorage)
    expect(fetcher.mock.calls).toHaveLength(1)
  })
  it('retains recovery when removing a successful receipt fails', async () => {
    preserveSkillMutation(intents()[3])
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => Response.json(options.method === 'GET' ? [skill] : receipt(3))))
    await mount(language)
    vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: sessionStorage.setItem.bind(sessionStorage), removeItem: () => { throw new Error('Blocked') } })
    await click(strings.verify)
    expect(container.textContent).toContain(strings.processStorage)
    expect(container.textContent).not.toContain(strings.saved)
    expect(sessionStorage.getItem(key)).not.toBeNull()
    expect(button(strings.create).disabled).toBe(true)
  })
})

it('isolates recovery by bee and ignores a late receipt after switching away', async () => {
  const intent = preserveSkillMutation(intents()[3])
  let finish!: (response: Response) => void
  const fetcher = vi.fn(async (url: string, options: RequestInit) => options.method === 'GET' ? Response.json(url.includes(other) ? [] : [skill]) : new Promise<Response>((resolve) => { finish = resolve }))
  vi.stubGlobal('fetch', fetcher)
  await mount('pt-BR')
  await act(async () => { button(skillResources['pt-BR'].verify).click(); button(skillResources['pt-BR'].verify).click() })
  expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1)
  await mount('pt-BR', other)
  expect(container.textContent).not.toContain(skillResources['pt-BR'].lost)
  expect(button(skillResources['pt-BR'].create).disabled).toBe(false)
  await act(async () => finish(Response.json(receipt(3))))
  expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual(intent)
  await mount('pt-BR')
  expect(container.textContent).toContain(skillResources['pt-BR'].lost)
  expect(container.textContent).not.toContain(skillResources['pt-BR'].saved)
})

it.each([408, 502])('retains the first uncertain HTTP %s response across reload', async (status) => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'GET' ? Response.json([]) : Response.json({ error: { code: 'unavailable' } }, { status })))
  await mount('pt-BR'); await importFile('pt-BR'); await reload('pt-BR')
  expect(sessionStorage.getItem(key)).not.toBeNull()
  expect(container.textContent).toContain(skillResources['pt-BR'].lost)
})

it('clears a definitive rejection of the first dispatch and permits reviewing the draft', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'GET' ? Response.json([]) : Response.json({ error: { code: 'invalid_request' } }, { status: 422 })))
  await mount('pt-BR'); await importFile('pt-BR')
  expect(sessionStorage.getItem(key)).toBeNull()
  expect(container.textContent).not.toContain(skillResources['pt-BR'].lost)
  expect(container.querySelector('.skill-editor')).not.toBeNull()
})
