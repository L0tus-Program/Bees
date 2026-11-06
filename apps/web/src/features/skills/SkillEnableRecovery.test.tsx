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
import { agentId, skill } from './fixtures'

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

describe.each(['pt-BR', 'en', 'es'] as const)('%s skill enable recovery', (language) => {
  it.each([true, false].flatMap((enabled) => ['state', 'hash', 'crypto'].map((failure) => ({ enabled, failure }))))('preserves enabled=$enabled after $failure until explicit verification', async ({ enabled, failure }) => {
    const strings = skillResources[language]
    const i18n = createInstance()
    await i18n.init({ lng: language, resources: { [language]: { skills: strings, product: productResources[language] } }, interpolation: { escapeValue: false } })
    let current = { ...skill, enabled_version: enabled ? null : 1 }
    let attempts = 0
    const writes: string[] = []
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'PUT') {
        writes.push(options.body as string); attempts++
        if (attempts === 1 && failure === 'state') return Response.json({ ...current, revision: 2 }) // opposite state
        if (attempts === 1 && failure === 'crypto') vi.spyOn(webcrypto.subtle, 'digest').mockRejectedValueOnce(new Error('Private failure'))
        current = { ...current, revision: 2, enabled_version: enabled ? 1 : null }
        return Response.json(attempts === 1 && failure === 'hash' ? { ...current, current: { ...current.current, content_hash: '0'.repeat(64) } } : current)
      }
      return Response.json([current])
    }); vi.stubGlobal('fetch', fetcher)
    await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id: agentId }} /></I18nextProvider>)); await settled()
    if (enabled) { await click(strings.enable); await click(strings.confirmEnable) }
    else await click(strings.disable)
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
    expect(container.querySelector('.skill-list')?.textContent).toContain(enabled ? strings.enabled : strings.disabled)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/chat'))).toBe(true)
  })
})
