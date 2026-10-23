// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { RoutineHook } from './RoutineHook'
import { hookResources } from '../../locales/hooks'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'

const hook = { id: 'h1', routine_id: 'r1', status: 'active', min_interval_seconds: 60, last_triggered_at: null, last_result: null, trigger_count: 0, revision: 1, created_at: 'x' }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { hooks: hookResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })

it('creates a trigger and shows the secret URL once', async () => {
  const posts: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(JSON.parse(options.body as string)); return Response.json({ hook, url: 'https://bees.example/api/v1/hooks/bh_secret' }, { status: 201 }) }
    return Response.json({ hook: null })
  }))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><RoutineHook agentId="a1" routineId="r1" /></I18nextProvider>))
  const details = container.querySelector('details')!
  await act(async () => { details.open = true; details.dispatchEvent(new Event('toggle')) })
  await vi.waitFor(() => expect(container.textContent).toContain('Sem gatilho'))
  await act(async () => [...container.querySelectorAll('button')].find((item) => item.textContent === 'Criar gatilho')!.click())
  expect(posts[0]).toEqual({ min_interval_seconds: 60 })
  expect(container.textContent).toContain('bh_secret')
  expect(container.textContent).toContain('não será mostrada de novo')
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(hookResources[language]).sort()).toEqual(Object.keys(hookResources['pt-BR']).sort())
})
