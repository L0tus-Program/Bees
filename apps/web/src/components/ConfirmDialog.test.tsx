// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { dialogResources } from '../locales/dialog'
import { ConfirmHost } from './ConfirmDialog'
import { confirmAction } from './confirmAction'

let root: Root, container: HTMLDivElement
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  const i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { dialog: dialogResources['pt-BR'] } } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ConfirmHost /></I18nextProvider>))
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
const buttons = () => [...container.querySelectorAll('dialog button')] as HTMLButtonElement[]

describe('accessible confirmation', () => {
  it('names the dialog, focuses cancel and resolves each answer in order', async () => {
    const native = vi.fn(); vi.stubGlobal('confirm', native)
    let first!: Promise<boolean>, second!: Promise<boolean>
    await act(async () => { first = confirmAction('Apagar modelo?', 'Apagar'); second = confirmAction('Revogar?') })
    const dialog = container.querySelector('dialog')!
    expect(dialog.getAttribute('aria-labelledby')).toBeTruthy()
    expect(document.getElementById(dialog.getAttribute('aria-describedby')!)!.textContent).toBe('Apagar modelo?')
    expect(buttons()[0].hasAttribute('autofocus') || document.activeElement === buttons()[0]).toBe(true)
    expect(buttons()[1].textContent).toBe('Apagar')
    await act(async () => buttons()[0].click())
    expect(await first).toBe(false)
    expect(container.querySelector('dialog p')!.textContent).toBe('Revogar?')
    await act(async () => buttons()[1].click())
    expect(await second).toBe(true)
    expect(container.querySelector('dialog')).toBeNull()
    expect(native).not.toHaveBeenCalled()
  })
  it('falls back to the native prompt without a host', async () => {
    await act(async () => root.unmount())
    vi.stubGlobal('confirm', vi.fn(() => true))
    expect(await confirmAction('Ok?')).toBe(true)
    root = createRoot(container)
  })
})
