// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { agent as chatAgent } from '../chat/testFixtures'
import { skillResources } from '../../locales/skills'
import { productResources } from '../../locales/product'
import { SkillsPanel } from './SkillsPanel'
import { agentId, definition, skill } from './fixtures'

let root: Root
let container: HTMLDivElement
beforeEach(() => {
  sessionStorage.clear()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
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

describe.each(['pt-BR', 'en', 'es'] as const)('%s skill save recovery', (language) => {
  it.each(['definition', 'hash', 'crypto'])('keeps an incompatible imported %s pending until explicit verification', async (failure) => {
    const strings = skillResources[language]
    const i18n = createInstance()
    await i18n.init({ lng: language, resources: { [language]: { skills: strings, product: productResources[language] } }, interpolation: { escapeValue: false } })
    const imported = { ...skill, current: { ...skill.current, origin: 'import' } }
    let attempts = 0
    const writes: string[] = []
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        writes.push(options.body as string); attempts++
        if (attempts === 1 && failure === 'crypto') vi.spyOn(webcrypto.subtle, 'digest').mockRejectedValueOnce(new Error('Private failure'))
        const invalid = failure === 'hash' ? { ...imported.current, content_hash: '0'.repeat(64) } : { ...imported.current, definition: { ...definition, output: 'Outra entrega' } }
        return Response.json(attempts === 1 && failure !== 'crypto' ? { ...imported, current: invalid } : imported)
      }
      return Response.json(attempts > 1 ? [imported] : [])
    }); vi.stubGlobal('fetch', fetcher)
    await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id: agentId }} /></I18nextProvider>)); await settled()
    const fileInput = container.querySelector('input[type=file]') as HTMLInputElement
    Object.defineProperty(fileInput, 'files', { configurable: true, value: [new File([JSON.stringify({ format: 'bees.skill/v1', scope: 'agent', definition })], 'procedure.json', { type: 'application/json' })] })
    await act(async () => fileInput.dispatchEvent(new Event('change', { bubbles: true })))
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); await settled()
    expect(attempts).toBe(1)
    expect(container.textContent).toContain(strings.lost)
    expect(container.textContent).not.toContain(strings.saved)
    expect(button(strings.create).disabled).toBe(true)
    await click(strings.refresh)
    expect(attempts).toBe(1)
    expect(button(strings.create).disabled).toBe(true)
    await click(strings.verify)
    expect(attempts).toBe(2)
    expect(writes[1]).toBe(writes[0])
    expect(container.textContent).toContain(strings.saved)
    expect(container.textContent).not.toContain(strings.lost)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/chat'))).toBe(true)
  })
  it.each(['list', 'history'])('does not display an inconsistent revision from %s', async (reading) => {
    const strings = skillResources[language]
    const i18n = createInstance()
    await i18n.init({ lng: language, resources: { [language]: { skills: strings, product: productResources[language] } }, interpolation: { escapeValue: false } })
    const current = { ...skill.current, content_hash: '0'.repeat(64) }
    const fetcher = vi.fn(async (url: string) => Response.json(url.includes('/revisions?') ? [current] : reading === 'list' ? [{ ...skill, current }] : [skill]))
    vi.stubGlobal('fetch', fetcher)
    await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id: agentId }} /></I18nextProvider>)); await settled()
    if (reading === 'history') await click(strings.history)
    expect(container.querySelector('[role=alert]')).not.toBeNull()
    expect(container.querySelector('.skill-history')).toBeNull()
    expect(container.querySelector('.skill-review')).toBeNull()
    if (reading === 'list') expect(container.querySelectorAll('.skill-list > li')).toHaveLength(0)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/enabled'))).toBe(true)
  })

})
