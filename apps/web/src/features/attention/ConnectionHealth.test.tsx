// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ConnectionHealth } from './ConnectionHealth'
import { healthResources } from '../../locales/health'
import { productResources } from '../../locales/product'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { health: healthResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })

it('explains each connection issue without raw codes', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ issues: [
    { kind: 'channel', id: 'c1', name: 'Bot', detail: 'telegram', code: 'channel_token_invalid', blocking: true, since: null },
    { kind: 'executor', id: 'e1', name: 'Notebook', detail: '2', code: 'executor_offline', blocking: true, since: null },
    { kind: 'mcp', id: 'm1', name: 'agenda', detail: null, code: 'algo_novo', blocking: true, since: null },
  ] })))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ConnectionHealth agentId="a1" /></I18nextProvider>))
  await vi.waitFor(() => expect(container.textContent).toContain('token do bot foi recusado'))
  expect(container.textContent).toContain('(2)')
  expect(container.textContent).toContain('Confira esta conexão')
  expect(container.textContent).not.toContain('algo_novo')
  expect(container.querySelectorAll('[role=alert]')).toHaveLength(3)
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(healthResources[language]).sort()).toEqual(Object.keys(healthResources['pt-BR']).sort())
})
