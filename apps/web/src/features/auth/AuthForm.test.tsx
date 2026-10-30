// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AuthForm } from './AuthForm'
import { productResources } from '../../locales/product'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
function button(text: string) { return [...container.querySelectorAll('button')].find((item) => item.textContent === text)! }
async function type(id: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`#${id}`)!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}

it('resets the password with an operator code and explains invalid codes', async () => {
  const calls: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    calls.push({ url, body: JSON.parse(options.body as string) })
    if (calls.length === 1) return Response.json({ error: { code: 'bootstrap_invalid', message: 'x' } }, { status: 401 })
    return Response.json({ configured: true, authenticated: true, user: { name: 'Teste' }, csrf_token: 'c'.repeat(43) })
  }))
  const onAuthenticated = vi.fn()
  await act(async () => root.render(<I18nextProvider i18n={i18n}><AuthForm configured onAuthenticated={onAuthenticated} /></I18nextProvider>))
  await act(async () => button('Esqueci a senha').click())
  expect(container.textContent).toContain('bees-auth recover')
  expect(container.querySelector('#confirm-password')).not.toBeNull()
  await type('recovery-token', 'A'.repeat(43)); await type('password', 'nova senha longa 123'); await type('confirm-password', 'nova senha longa 123')
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(calls[0].url).toContain('/auth/recover')
  expect(calls[0].body).toEqual({ recovery_token: 'A'.repeat(43), password: 'nova senha longa 123' })
  expect(container.textContent).toContain('Gere outro no servidor')
  await type('recovery-token', 'B'.repeat(43))
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(onAuthenticated).toHaveBeenCalled()
})
