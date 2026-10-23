// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ChannelsPanel } from './ChannelsPanel'
import { channelResources } from '../../locales/channels'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'

const channel = { id: 'c1', agent_id: 'bee', conversation_id: 'conv', kind: 'telegram', name: 'Bee', status: 'pending', mode: 'chat', target: null, paired: false, peer_name: null, bot_username: 'bee_bot', pairing_expires_at: '2026-10-23T12:00:00Z', reason_code: null, last_activity_at: null, revision: 1, created_at: 'x', updated_at: 'x' }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { channels: channelResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })
async function type(selector: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(selector)!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })) })
}

it('connects a Telegram bot and shows the one-time pairing code and link', async () => {
  const posts: unknown[] = []
  let listed: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push(JSON.parse(options.body as string)); listed = [channel]; return Response.json({ channel, pairing_code: 'ABCD2345', pairing_link: 'https://t.me/bee_bot?start=ABCD2345', replayed: false }, { status: 201 }) }
    return Response.json({ channels: listed })
  }))
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ChannelsPanel agentId="bee" /></I18nextProvider>))
  await vi.waitFor(() => expect(container.textContent).toContain('Nenhum canal conectado'))
  await type('#channel-token', '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ')
  await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
  expect(posts[0]).toMatchObject({ kind: 'telegram', token: '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ', mode: 'chat' })
  await vi.waitFor(() => expect(container.textContent).toContain('ABCD2345'))
  expect(container.querySelector<HTMLAnchorElement>('a[href^="https://t.me/"]')?.href).toBe('https://t.me/bee_bot?start=ABCD2345')
  expect((container.querySelector('#channel-token') as HTMLInputElement).value).toBe('')
  expect(container.textContent).toContain('aguardando pareamento')
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(channelResources[language]).sort()).toEqual(Object.keys(channelResources['pt-BR']).sort())
})
