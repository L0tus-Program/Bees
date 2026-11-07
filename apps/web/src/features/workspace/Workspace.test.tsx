// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { productResources } from '../../locales/product'
import { workspaceResources } from '../../locales/workspace'
import { agentId, conversationId } from '../artifacts/fixtures'
import { agent as baseAgent, stamp } from '../chat/testFixtures'
import { Workspace } from './Workspace'

const requestId = 'c6827f60-d208-4ba6-a30a-74bc3d2c46a8'
const oldMessage = { id: 'old-message', role: 'user', content: 'Histórico anterior à limpeza', created_at: stamp }

it.each([false, true])('retries the active conversation read without replaying the clear (changed: %s)', async (changed) => {
  const agent = { ...baseAgent, id: agentId, conversation_id: conversationId }
  const active = (id: string) => Response.json({
    agents: [{ ...agent, conversation_id: id }], vault: { available: true, backend: 'test' }, environments: [], next_steps: [],
  })
  const newId = '0e06fa34-a622-5678-8b32-d25582b3b08f'
  const initial = active(conversationId)
  const unavailable = Response.json({ error: { code: 'http_503' } }, { status: 503 })
  const reads = vi.fn().mockResolvedValueOnce(initial).mockResolvedValueOnce(unavailable)
  let finishRead!: (response: Response) => void
  reads.mockImplementationOnce(() => new Promise<Response>((resolve) => { finishRead = resolve }))
  const clear = vi.fn(async () => changed ? Response.json({ conversation_id: newId }) : Response.json({ error: { code: 'http_502' } }, { status: 502 }))
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.stubGlobal('crypto', { randomUUID: () => requestId, subtle: webcrypto.subtle })
  vi.stubGlobal('confirm', () => true)
  vi.stubGlobal('fetch', vi.fn(async (path: string) => {
    if (path.endsWith('/onboarding')) return reads()
    if (path.endsWith('/clear')) return clear()
    if (path.includes('/messages?')) {
      const id = new URL(path, 'http://localhost').searchParams.get('conversation_id')
      return Response.json({ conversation_id: id, revision: 1, messages: id === conversationId ? [oldMessage] : [] })
    }
    if (path.endsWith('/agent/capabilities')) return Response.json({
      available: true, tool_calls_supported: true, recommended_installed: true, tools: [],
      worker: { available: true, last_seen: null }, defaults: { max_calls: 30, max_active_seconds: 1200 },
    })
    if (path.includes('/delegations')) return Response.json({ delegations: [], has_more: false, next_offset: null })
    if (path.includes('/tasks')) return Response.json({ tasks: [], has_more: false })
    return Response.json({})
  }))
  setCsrfToken('csrf')
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { product: productResources['pt-BR'], workspace: workspaceResources['pt-BR'] } } })
  const container = document.createElement('div')
  document.body.append(container)
  const root = createRoot(container)
  const button = (name: string) => [...container.querySelectorAll('button')].find((node) => node.textContent?.trim() === name)
  const send = () => container.querySelector<HTMLButtonElement>('button[type="submit"]')
  try {
    await act(async () => root.render(<I18nextProvider i18n={i18n}><Workspace onData={vi.fn()} onSignOut={vi.fn()} signingOut={false} /></I18nextProvider>))
    const input = container.querySelector<HTMLTextAreaElement>('#chat-input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Meu rascunho')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(send()?.disabled).toBe(false)
    await act(async () => {
      button('Limpar conversa')!.click()
      await vi.waitFor(() => expect(clear).toHaveBeenCalledOnce())
    })
    expect(reads).toHaveBeenCalledTimes(2)
    expect(container.textContent).toContain(oldMessage.content)
    expect(container.querySelector('#chat-input')).toBe(input)
    expect(input.value).toBe('Meu rascunho')
    expect(send()?.disabled).toBe(true)
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="Anexar arquivo"]')?.disabled).toBe(true)
    expect(button(changed ? 'Limpando…' : 'Limpar conversa')?.disabled).toBe(true)

    await act(async () => button('Tentar de novo')!.click())
    expect(reads).toHaveBeenCalledTimes(3)
    expect(button('Tentar de novo')?.disabled).toBe(true)
    expect(send()?.disabled).toBe(true)
    await act(async () => finishRead(active(changed ? newId : conversationId)))

    expect(clear).toHaveBeenCalledOnce()
    expect(button('Tentar de novo')).toBeUndefined()
    if (changed) {
      expect(container.textContent).not.toContain(oldMessage.content)
      expect(container.querySelector<HTMLTextAreaElement>('#chat-input')?.value).toBe('')
    } else {
      expect(container.textContent).toContain(oldMessage.content)
      expect(input.value).toBe('Meu rascunho')
      expect(send()?.disabled).toBe(false)
    }
  } finally {
    await act(async () => root.unmount())
    container.remove()
    setCsrfToken()
    vi.unstubAllGlobals()
  }
})
