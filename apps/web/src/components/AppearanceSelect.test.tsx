// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { productResources } from '../locales/product'
import { AppearanceSelect } from './AppearanceSelect'
import { appearanceKey, applyAppearance, readAppearance } from './appearance'

let root: Root, container: HTMLDivElement, dark: boolean
let media: EventTarget
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  dark = false; media = new EventTarget(); localStorage.clear()
  vi.stubGlobal('matchMedia', vi.fn(() => media))
  // The mock's getter must remain live when the operating-system preference changes.
  Object.defineProperty(media, 'matches', { get: () => dark, configurable: true })
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); delete document.documentElement.dataset.theme; document.documentElement.style.colorScheme = '' })
async function render(language: keyof typeof productResources = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { product: productResources[language] } } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><AppearanceSelect /></I18nextProvider>))
}
async function choose(value: string) { await act(async () => { container.querySelector('select')!.value = value; container.querySelector('select')!.dispatchEvent(new Event('change', { bubbles: true })) }) }
async function system(value: boolean) { await act(async () => { dark = value; media.dispatchEvent(new Event('change')) }) }
describe('browser appearance preference', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('names the control and browser scope in %s without making requests', async (language) => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await render(language)
    expect(container.textContent).toContain(productResources[language].appearance)
    expect(container.textContent).toContain(productResources[language].appearanceScope)
    expect(container.querySelector('select')!.value).toBe('system')
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('follows system changes only in system mode and restores an explicit choice', async () => {
    await render(); await system(true); expect(document.documentElement.dataset.theme).toBe('dark')
    await choose('light'); await system(true); expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem(appearanceKey)).toBe('light')
    applyAppearance(readAppearance()); expect(document.documentElement.style.colorScheme).toBe('light')
    await choose('system'); expect(document.documentElement.dataset.theme).toBe('dark')
    await system(false); expect(document.documentElement.dataset.theme).toBe('light')
  })
  it('synchronizes another tab, handles clearing storage, and ignores unrelated or session storage', async () => {
    await render()
    async function storage(key: string | null, newValue: string | null, storageArea = localStorage) { await act(async () => window.dispatchEvent(new StorageEvent('storage', { key, newValue, storageArea }))) }
    await storage(appearanceKey, 'dark'); expect(document.documentElement.dataset.theme).toBe('dark')
    await storage('unrelated', 'light'); await storage(appearanceKey, 'light', sessionStorage); expect(document.documentElement.dataset.theme).toBe('dark')
    await storage(null, null); expect(document.documentElement.dataset.theme).toBe('light')
    expect(container.querySelector('select')!.value).toBe('system')
  })
  it('keeps the tab usable and reports an unsaved choice when browser storage is blocked', async () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } })
    await render(); await choose('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(container.querySelector('[role=status]')?.textContent).toBe(productResources['pt-BR'].appearanceUnsaved)
  })
  it('ignores malformed stored preferences and removes its system listener when unmounted', async () => {
    localStorage.setItem(appearanceKey, 'unexpected')
    const remove = vi.spyOn(media, 'removeEventListener')
    await render(); expect(container.querySelector('select')!.value).toBe('system')
    await act(async () => root.render(null)); expect(remove).toHaveBeenCalledWith('change', expect.any(Function))
  })
})
