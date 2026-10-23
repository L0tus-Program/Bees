// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LocalModelPanel } from './LocalModelPanel'
import { localModelResources } from '../../locales/localModel'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'

const status = {
  endpoint: 'http://127.0.0.1:11434', reachable: true, version: '0.40.6',
  models: [{ name: 'qwen3:1.7b', size_bytes: 1400000000, modified_at: null }],
  suggested: [
    { id: 'qwen3:1.7b', name: 'Qwen 3 1.7B', download_bytes: 1400000000, min_memory_gb: 4, tool_calls: true, source: 'https://ollama.com/library/qwen3', note: 'small' },
    { id: 'qwen3:8b', name: 'Qwen 3 8B', download_bytes: 5200000000, min_memory_gb: 12, tool_calls: true, source: 'https://ollama.com/library/qwen3', note: 'quality' },
  ],
  pulls: [],
}
const pull = { id: 'p1', model: 'qwen3:8b', endpoint: status.endpoint, status: 'running', phase: 'downloading', total_bytes: 100, completed_bytes: 40, error: null, started_at: 'x', finished_at: null }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { localModel: localModelResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render(onSelect = vi.fn()) {
  await act(async () => root.render(<I18nextProvider i18n={i18n}><LocalModelPanel endpoint="http://127.0.0.1:11434" model="" onSelect={onSelect} /></I18nextProvider>))
  return onSelect
}
function button(text: string) { return [...container.querySelectorAll('button')].find((item) => item.textContent === text) }

it('lists installed models and selects one', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(status)))
  const onSelect = await render()
  await vi.waitFor(() => expect(container.textContent).toContain('Ollama conectado (versão 0.40.6)'))
  await act(async () => button('Usar este modelo')!.click())
  expect(onSelect).toHaveBeenCalledWith('qwen3:1.7b')
})

it('asks for confirmation with size and source before downloading, then follows progress', async () => {
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(JSON.parse(options.body as string)); return Response.json(pull, { status: 202 }) }
    if (url.includes('/pulls/p1')) return Response.json({ ...pull, status: 'completed', phase: 'success', completed_bytes: 100 })
    return Response.json(status)
  }))
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  await render()
  await vi.waitFor(() => expect(button('Baixar')).toBeTruthy())
  await act(async () => button('Baixar')!.click())
  expect(confirm.mock.calls[0][0]).toContain('5,2 GB')
  expect(confirm.mock.calls[0][0]).toContain('https://ollama.com/library/qwen3')
  expect(writes).toHaveLength(0)
  await act(async () => button('Baixar')!.click())
  expect(writes[0]).toMatchObject({ model: 'qwen3:8b', confirm_download: true, endpoint: 'http://127.0.0.1:11434' })
  expect(button('Cancelar download')).toBeTruthy()
  expect(container.querySelector('progress')?.getAttribute('value')).toBe('40')
  await vi.waitFor(() => expect(container.textContent).toContain('qwen3:8b está pronto'), { timeout: 3000 })
})

it('explains how to start Ollama when unreachable and offers no download', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...status, reachable: false, version: null, models: [] })))
  await render()
  await vi.waitFor(() => expect(container.textContent).toContain('--profile local-model'))
  expect(button('Baixar')).toBeUndefined()
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(localModelResources[language]).sort()).toEqual(Object.keys(localModelResources['pt-BR']).sort())
})
