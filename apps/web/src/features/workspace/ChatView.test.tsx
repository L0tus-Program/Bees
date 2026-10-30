// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { DelegationCollection } from '../../api/delegations'
import type { Message } from '../../api/onboarding'
import { setCsrfToken } from '../../api/client'
import { agentResources } from '../../locales/agent'
import { productResources } from '../../locales/product'
import { workspaceResources } from '../../locales/workspace'
import { useResource } from '../../hooks/useResource'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { agent as baseAgent, task as baseTask } from '../chat/testFixtures'
import { agentId, artifact, conversationId, page, taskId } from '../artifacts/fixtures'
import { ChatView } from './ChatView'

vi.mock('../tasks/useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
vi.mock('../../hooks/useResource', () => ({ useResource: vi.fn() }))

const agent = { ...baseAgent, id: agentId, conversation_id: conversationId }
const stamp = '2026-10-30T12:00:00Z'
const user: Message = { id: 'message-user', role: 'user', content: 'Gere um relatório', created_at: stamp }
const reply: Message = { id: 'message-assistant', role: 'assistant', content: 'Pronto, **relatório** gerado.', created_at: stamp }
const turnTask = { ...baseTask, id: taskId, agent_id: agentId, execution_kind: 'agent_task', status: 'completed' as const, agent_steps: [{ tool: 'publish_file', status: 'confirmed', code: null, at: stamp }] }
const turn = { id: taskId, source_message_id: user.id, response_message_id: user.id, task: turnTask }

let root: Root, container: HTMLDivElement, reload: ReturnType<typeof vi.fn>
let delegations: DelegationCollection | null
let messages: Message[]
let capabilities: Record<string, unknown>
let fetcher: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  delegations = { delegations: [], has_more: false, next_offset: null }; messages = []
  capabilities = { available: true, tool_calls_supported: true, recommended_installed: true, tools: [], worker: { available: true, last_seen: null }, defaults: { max_calls: 30, max_active_seconds: 1200 } }
  reload = vi.fn()
  vi.mocked(useResource).mockImplementation(() => ({ resource: { state: 'ready', data: { conversation_id: conversationId, messages } }, reload }) as never)
  vi.mocked(useTaskPolling).mockImplementation(() => ({ data: delegations, loading: false, error: null, refresh: vi.fn() }) as never)
  fetcher = vi.fn(async (url: string) => {
    if (url.includes('/agent/capabilities')) return Response.json(capabilities)
    if (url.includes('/artifacts')) return Response.json(page([artifact]))
    return Response.json({ task_id: taskId, message_id: 'm', status: 'queued' })
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })

async function show() {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { workspace: workspaceResources['pt-BR'], agent: agentResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ChatView agent={agent} onTaskDetails={vi.fn()} /></I18nextProvider>))
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)) })
}
const calls = (fragment: string) => fetcher.mock.calls.filter(([url]) => String(url).includes(fragment))

describe('chat as an assistant conversation', () => {
  it('sends with Enter to the agent and keeps Shift+Enter for a new line', async () => {
    await show()
    const input = container.querySelector('#chat-input') as HTMLTextAreaElement
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Oi'); input.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true, cancelable: true })) })
    expect(calls('/agent-messages')).toHaveLength(0)
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)) })
    expect(calls('/agent-messages')).toHaveLength(1)
    expect(JSON.parse((calls('/agent-messages')[0] as [string, RequestInit])[1].body as string)).toMatchObject({ content: 'Oi' })
  })

  it('shows the agent turn as steps, then the reply, then the files to download — no task jargon', async () => {
    messages = [user, reply]; delegations = { delegations: [turn], has_more: false, next_offset: null }
    await show()
    const text = container.textContent ?? ''
    expect(text.indexOf('Gere um relatório')).toBeLessThan(text.indexOf('Usou 1 ferramenta'))
    expect(text.indexOf('Usou 1 ferramenta')).toBeLessThan(text.indexOf('relatório gerado'))
    const link = container.querySelector('a[download]') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe(`/api/v1/agents/${agentId}/artifacts/${artifact.id}/download`)
    expect(text.indexOf('relatório gerado')).toBeLessThan(text.indexOf(artifact.name))
    expect(container.querySelector('strong')?.textContent).toBe('relatório')
    expect(text).not.toMatch(/Tarefa|tarefa do agente/)
  })

  it('does not reload for agent results that already existed when the chat opened, only for new ones', async () => {
    delegations = null
    await show()
    delegations = { delegations: [turn], has_more: false, next_offset: null }
    await show()
    expect(reload).not.toHaveBeenCalled()
    delegations = { delegations: [turn, { ...turn, id: 'other', task: { ...turnTask, id: 'other' } }], has_more: false, next_offset: null }
    await show()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('turns on the recommended tools once when the model supports tool calls', async () => {
    capabilities = { ...capabilities, available: false, recommended_installed: false }
    await show()
    await show()
    expect(calls('/agent/recommended-tools')).toHaveLength(1)
  })

  it('keeps the newest message last: old turns outside the window and standalone tasks never pile up at the end', async () => {
    const old = { ...turn, id: 'old', source_message_id: 'gone', task: { ...turnTask, id: 'old', created_at: '2026-10-29T10:00:00Z' } }
    const standalone = { id: 'side', source_message_id: user.id, response_message_id: reply.id, task: { ...baseTask, id: 'side', agent_id: agentId, title: 'Planilha de custos', status: 'running' as const } }
    const later: Message = { id: 'message-later', role: 'user', content: 'Mensagem nova', created_at: '2026-10-30T12:05:00Z' }
    messages = [user, reply, later]; delegations = { delegations: [standalone, old], has_more: false, next_offset: null }
    await show()
    const text = container.querySelector('[role="log"]')?.textContent ?? ''
    expect(text.trimEnd().endsWith('Mensagem nova')).toBe(true)
    expect(text.indexOf('Planilha de custos')).toBeGreaterThan(text.indexOf('Gere um relatório'))
    expect(text.indexOf('Planilha de custos')).toBeLessThan(text.indexOf('Mensagem nova'))
    expect(container.querySelectorAll('[aria-expanded]')).toHaveLength(0)
  })

  it('scrolls only the message list, never the page around it', async () => {
    const spy = vi.fn()
    Element.prototype.scrollIntoView = spy
    messages = [user, reply]
    await show()
    expect(spy).not.toHaveBeenCalled()
  })

  it('updates the recommended tools once when the bee already has tools but a newer bundle exists', async () => {
    capabilities = { ...capabilities, available: true, recommended_installed: false }
    await show()
    await show()
    expect(calls('/agent/recommended-tools')).toHaveLength(1)
  })

  it('leaves the tools alone when the recommended bundle is current', async () => {
    await show()
    expect(calls('/agent/recommended-tools')).toHaveLength(0)
  })
})
