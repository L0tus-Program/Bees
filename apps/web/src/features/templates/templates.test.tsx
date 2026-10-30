// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TemplatePicker } from './TemplatePicker'
import { BUILTIN_TEMPLATES, parseTemplate, serializeTemplate } from './templates'
import { templateResources } from '../../locales/templates'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { templates: templateResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })

it('round-trips a template and rejects foreign or oversized files', () => {
  const template = { name: 'Radar', purpose: 'Notícias', instructions: 'Resuma.' }
  expect(parseTemplate(serializeTemplate(template))).toEqual(template)
  expect(parseTemplate(JSON.stringify({ ...template, format: 'other' }))).toBeNull()
  expect(parseTemplate(JSON.stringify({ format: 'bees.bee-template/v1', name: '', purpose: 'x' }))).toBeNull()
  expect(parseTemplate('{not json')).toBeNull()
  expect(parseTemplate(JSON.stringify({ format: 'bees.bee-template/v1', name: 'a', purpose: 'x'.repeat(2001) }))).toBeNull()
  // Campos extras (credenciais, permissões) são ignorados.
  expect(parseTemplate(JSON.stringify({ format: 'bees.bee-template/v1', name: 'a', purpose: 'b', api_key: 'sk-x' }))).toEqual({ name: 'a', purpose: 'b', instructions: '' })
})

it('applies a built-in template', async () => {
  const applied = vi.fn()
  await act(async () => root.render(<I18nextProvider i18n={i18n}><TemplatePicker onApply={applied} /></I18nextProvider>))
  const select = container.querySelector('select')!
  await act(async () => { select.value = 'researcher'; select.dispatchEvent(new Event('change', { bubbles: true })) })
  expect(applied).toHaveBeenCalledWith(expect.objectContaining({ name: 'Pesquisadora' }))
  expect(container.textContent).toContain('modo agente')
})

it.each(['pt-BR', 'en', 'es'] as const)('defines every built-in template in %s', (language) => {
  for (const item of BUILTIN_TEMPLATES) {
    const entry = templateResources[language].templates[item]
    expect(entry.name && entry.purpose && entry.instructions && entry.label).toBeTruthy()
    expect(entry.purpose.length).toBeLessThanOrEqual(2000)
  }
})
