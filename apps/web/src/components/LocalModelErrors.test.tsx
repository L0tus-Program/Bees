// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, request, setCsrfToken } from '../api/client'
import { productResources } from '../locales/product'
import { ErrorNotice } from './Feedback'

const codes = ['backend_unavailable', 'model_not_found', 'insufficient_storage', 'pull_failed', 'pull_not_found', 'pull_conflict', 'invalid_model', 'model_in_use', 'registry_unreachable']
const cases = ['pt-BR', 'en', 'es'].flatMap((language) => codes.map((code) => [language, code]))
let root: Root
let container: HTMLDivElement
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  setCsrfToken('csrf')
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })

it.each(cases)('renders %s %s with localized guidance, focus and no raw response', async (language, code) => {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: false, resources: Object.fromEntries(Object.entries(productResources).map(([lang, product]) => [lang, { product }])), interpolation: { escapeValue: false } })
  const fetcher = vi.fn(async () => Response.json({ error: { code, message: 'RAW_PRIVATE_SERVER_MESSAGE' } }, { status: 409 }))
  vi.stubGlobal('fetch', fetcher)
  const error = await request('/local-models/delete', { authenticated: true, body: { model: 'qwen3', confirm_delete: true } }).catch((error) => error)
  expect(error).toBeInstanceOf(ApiError)
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ErrorNotice error={error as ApiError} /></I18nextProvider>))
  const alert = container.querySelector('[role="alert"]')
  expect(alert?.textContent).toBe(i18n.t(`errors.${code}`, { ns: 'product' }))
  expect(alert?.textContent).not.toBe(i18n.t('errors.unknown_error', { ns: 'product' }))
  expect(container.textContent).not.toContain('RAW_PRIVATE_SERVER_MESSAGE')
  expect(document.activeElement).toBe(alert?.querySelector('p'))
  expect(fetcher).toHaveBeenCalledTimes(1)
})
