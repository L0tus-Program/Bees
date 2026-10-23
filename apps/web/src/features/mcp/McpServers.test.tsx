// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { McpServers } from './McpServers'
import { mcpResources } from '../../locales/mcp'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'

const server = { id: 's1', agent_id: 'bee', name: 'agenda', url: 'https://mcp.example.org/mcp', has_token: true, enabled: true, status: 'ready', reason_code: null, server_name: 'Agenda', tools: [{ name: 'list_events', description: 'Eventos', read_only: true }], refreshed_at: null, revision: 1 }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { mcp: mcpResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })
async function type(selector: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(selector)!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}

it('registers a connector and lists its tools', async () => {
  const posts: unknown[] = []
  let listed: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(JSON.parse(options.body as string)); listed = [server]; return Response.json(server, { status: 201 }) }
    return Response.json({ servers: listed })
  }))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><McpServers agentId="bee" /></I18nextProvider>))
  await vi.waitFor(() => expect(container.textContent).toContain('Nenhum conector MCP'))
  await type('#mcp-name', 'agenda'); await type('#mcp-url', 'https://mcp.example.org/mcp'); await type('#mcp-token', 'segredo')
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(posts[0]).toMatchObject({ name: 'agenda', url: 'https://mcp.example.org/mcp', token: 'segredo' })
  await vi.waitFor(() => expect(container.textContent).toContain('list_events'))
  expect(container.textContent).toContain('somente leitura')
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(mcpResources[language]).sort()).toEqual(Object.keys(mcpResources['pt-BR']).sort())
})
