// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { beeResources } from '../../locales/bee'
import { productResources } from '../../locales/product'
import { templateResources } from '../../locales/templates'
import { agent } from '../chat/testFixtures'
import { CreateBee } from '../setup/CreateBee'

const endpoint = 'https://api.openai.com/v1'
const credential = 'vault:11111111-1111-4111-8111-111111111111'
let root: Root, container: HTMLDivElement, fetcher: ReturnType<typeof vi.fn>, created: ReturnType<typeof vi.fn<(value: unknown) => void>>
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container); created = vi.fn()
  fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/providers')) return Response.json({ providers: [
      { id: 'openai', name: 'OpenAI', kind: 'openai_compatible', endpoint, requires_api_key: true, models: [{ id: 'gpt-5.6-terra', name: 'Terra' }] },
      { id: 'custom', name: 'Custom', kind: 'openai_compatible', endpoint: '', requires_api_key: true, models: [] },
    ] })
    if (url.endsWith('/model-credentials')) return Response.json({ credentials: [{ id: credential, kind: 'openai_compatible', endpoint }] })
    if (url.endsWith('/models/prepare')) return Response.json({ validation_token: 'receipt' })
    if (url.endsWith('/agents')) return Response.json(agent, { status: 201 })
    throw new Error('Unexpected test request')
  }); vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })
async function show() {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { bee: beeResources['pt-BR'], product: productResources['pt-BR'], templates: templateResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><CreateBee vault={{ available: true, backend: 'test' }} onCreated={(value) => created(value)} onReconcile={vi.fn()} /></I18nextProvider>))
}
async function change(selector: string, value: string) {
  const element = container.querySelector(selector) as HTMLInputElement
  const prototype = element.tagName === 'SELECT' ? HTMLSelectElement.prototype : element.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  await act(async () => { Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value); element.dispatchEvent(new Event(element.tagName === 'SELECT' ? 'change' : 'input', { bubbles: true })) })
}
it('creates another bee using the saved account credential without reentering a key', async () => {
  await show(); await change('#provider-kind', 'openai'); await change('#saved-credential', credential)
  expect(container.querySelector('#api-key')).toBeNull()
  await change('#model-id', 'gpt-5.6-terra'); await change('#bee-name', 'Segunda'); await change('#bee-purpose', 'Organizar')
  await act(async () => { container.querySelectorAll('form')[1].dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
  expect(created).toHaveBeenCalledOnce()
  const bodies = fetcher.mock.calls.filter(([url]) => url.endsWith('/models/prepare') || url.endsWith('/agents')).map(([, options]) => JSON.parse(options.body))
  expect(bodies).toHaveLength(2)
  for (const body of bodies) { expect(body.credential_id).toBe(credential); expect(body.config.endpoint).toBe(endpoint); expect(body).not.toHaveProperty('api_key'); expect(body.config).not.toHaveProperty('secret_ref') }
})
it('resets credential selection on a destination change, including when returning to the same endpoint', async () => {
  await show(); await change('#provider-kind', 'openai'); await change('#saved-credential', credential); await change('#provider-kind', 'custom')
  expect(container.querySelector('#saved-credential')).toBeNull()
  expect((container.querySelector('#api-key') as HTMLInputElement).value).toBe('')
  await change('#model-endpoint', endpoint)
  expect((container.querySelector('#saved-credential') as HTMLSelectElement).value).toBe('')
  expect(container.querySelector('#api-key')).not.toBeNull()
})

it('saves an explicit Responses selection and resets it when the provider changes', async () => {
  await show(); await change('#provider-kind', 'openai'); await change('#saved-credential', credential)
  expect((container.querySelector('#model-api') as HTMLSelectElement).value).toBe('auto')
  await change('#model-api', 'responses')
  await change('#model-id', 'gpt-5.6-terra'); await change('#bee-name', 'Responses'); await change('#bee-purpose', 'Organizar')
  await act(async () => { container.querySelectorAll('form')[1].dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
  const bodies = fetcher.mock.calls.filter(([url]) => url.endsWith('/models/prepare') || url.endsWith('/agents')).map(([, options]) => JSON.parse(options.body))
  expect(bodies).toHaveLength(2)
  for (const body of bodies) expect(body.config.api).toBe('responses')
  await change('#provider-kind', 'custom')
  expect((container.querySelector('#model-api') as HTMLSelectElement).value).toBe('auto')
})
