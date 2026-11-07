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
import { publishArtifact } from '../artifacts/api'
import { ChatView } from './ChatView'
import { registerConfirmHost } from '../../components/confirmAction'
import type { ConfirmRequest } from '../../components/confirmAction'
import { TooltipProvider } from '@/components/ui/tooltip'

vi.mock('../tasks/useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
vi.mock('../../hooks/useResource', () => ({ useResource: vi.fn() }))
vi.mock('../artifacts/api', async (original) => ({
  ...(await original<typeof import('../artifacts/api')>()),
  // Recibo coerente com a intenção: o servidor guardou exatamente o arquivo escolhido.
  publishArtifact: vi.fn(async (intent: { id: string; agentId: string; conversationId: string; name: string; media_type: string; size_bytes: number; sha256: string }) => ({
    id: intent.id, agent_id: intent.agentId, conversation_id: intent.conversationId, task_id: null, run_id: null, name: intent.name, media_type: intent.media_type,
    size_bytes: intent.size_bytes, sha256: intent.sha256, source: 'user_upload', version: 1, series_id: intent.id, previous_id: null, status: 'ready', revision: 1,
    created_at: '2026-10-30T12:00:00Z', updated_at: '2026-10-30T12:00:00Z',
  })),
}))

const agent = { ...baseAgent, id: agentId, conversation_id: conversationId }
const stamp = '2026-10-30T12:00:00Z'
const user: Message = { id: 'message-user', role: 'user', content: 'Gere um relatório', created_at: stamp }
const reply: Message = { id: 'message-assistant', role: 'assistant', content: 'Pronto, **relatório** gerado.', created_at: stamp, agent_task_id: taskId }
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
  vi.mocked(useResource).mockImplementation(() => ({ resource: { state: 'ready', data: { conversation_id: conversationId, revision: 3, messages } }, reload }) as never)
  vi.mocked(useTaskPolling).mockImplementation(() => ({ data: delegations, loading: false, error: null, refresh: vi.fn() }) as never)
  fetcher = vi.fn(async (url: string) => {
    if (url.includes('/agent/capabilities')) return Response.json(capabilities)
    if (url.includes('/artifacts')) return Response.json(page([artifact]))
    return Response.json({ task_id: taskId, message_id: 'm', status: 'queued' })
  })
  vi.stubGlobal('fetch', fetcher)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); registerConfirmHost(null); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })

async function show(onCleared?: () => void) {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { workspace: workspaceResources['pt-BR'], agent: agentResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><TooltipProvider><ChatView agent={agent} onTaskDetails={vi.fn()} onCleared={onCleared} /></TooltipProvider></I18nextProvider>))
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)) })
}
const calls = (fragment: string) => fetcher.mock.calls.filter(([url]) => String(url).includes(fragment))

function transferEvent(type: 'paste' | 'drop' | 'dragenter' | 'dragleave', files: File[] = [], itemsOnly = false) {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(event, type === 'paste' ? 'clipboardData' : 'dataTransfer', { value: {
    types: files.length ? ['Files'] : ['text/plain'], files: itemsOnly ? [] : files,
    items: files.map((file) => ({ kind: 'file', getAsFile: () => file })),
  } })
  return event
}

async function flushUploads() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 25)) }) }

describe('chat as an assistant conversation', () => {
  it('keeps the draft and waits for capabilities without switching to direct chat', async () => {
    let resolve!: (value: Response) => void
    const previous = fetcher.getMockImplementation() as (...args: unknown[]) => Promise<Response>
    fetcher.mockImplementation((...args: unknown[]) => String(args[0]).includes('/agent/capabilities') ? new Promise<Response>((done) => { resolve = done }) : previous(...args))
    await show()
    const input = container.querySelector('#chat-input') as HTMLTextAreaElement
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Use uma ferramenta'); input.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
    expect(calls('/agent-messages')).toHaveLength(0)
    expect(calls('/chat')).toHaveLength(0)
    expect(container.textContent).toContain('Preparando conversa')
    expect(input.value).toBe('Use uma ferramenta')
    await act(async () => resolve(Response.json(capabilities)))
    expect(calls('/agent-messages')).toHaveLength(0)
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
    expect(calls('/agent-messages')).toHaveLength(1)
    expect(calls('/chat')).toHaveLength(0)
  })

  it('shows capability lookup failures and retries without silently using direct chat', async () => {
    const previous = fetcher.getMockImplementation() as (...args: unknown[]) => Promise<Response>
    let failed = true
    fetcher.mockImplementation((...args: unknown[]) => {
      if (String(args[0]).includes('/agent/capabilities') && failed) return Promise.reject(new TypeError('offline'))
      return previous(...args)
    })
    await show()
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(calls('/chat')).toHaveLength(0)
    failed = false
    await act(async () => { const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Tentar de novo')!; retry.click() })
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(calls('/chat')).toHaveLength(0)
  })

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
    const link = container.querySelector(`button[aria-label="Baixar ${artifact.name}"]`)!
    expect(link.tagName).toBe('BUTTON')
    expect(link.getAttribute('href')).toBeNull()
    expect(text.indexOf('relatório gerado')).toBeLessThan(text.indexOf(artifact.name))
    expect(container.querySelector('strong')?.textContent).toBe('relatório')
    expect(text).not.toMatch(/Tarefa|tarefa do agente/)
  })

  it('keeps deliveries on their own turns when replies arrive out of order', async () => {
    const otherTaskId = 'a4cb3735-a4ef-49f4-9631-e0602c9a1eab'
    const secondUser = { ...user, id: 'second-user', content: 'Gere a planilha' }
    const directReply = { ...reply, id: 'direct-reply', content: 'Resposta de conversa', agent_task_id: null }
    const secondReply = { ...reply, id: 'second-reply', content: 'Planilha pronta', agent_task_id: otherTaskId }
    const otherTurn = { ...turn, id: otherTaskId, source_message_id: secondUser.id, task: { ...turnTask, id: otherTaskId } }
    delegations = { delegations: [turn, otherTurn], has_more: false, next_offset: null }
    const previous = fetcher.getMockImplementation() as (url: string) => Promise<Response>
    fetcher.mockImplementation((url: string) => url.includes(`/tasks/${otherTaskId}/artifacts`)
      ? Response.json(page([{ ...artifact, name: 'planilha.csv', task_id: otherTaskId }]))
      : previous(url))

    messages = [user, secondUser, directReply, secondReply]
    await show()
    const rows = () => Array.from(container.querySelector('[role="log"] > div')!.children)
    const downloads = () => rows().map((row) => Array.from(row.querySelectorAll('button[aria-label^="Baixar "]'), (button) => button.getAttribute('aria-label')))
    expect(downloads()).toEqual([[`Baixar ${artifact.name}`], [], [], ['Baixar planilha.csv']])

    messages = [...messages, reply]
    await show()
    expect(downloads()).toEqual([[], [], [], ['Baixar planilha.csv'], [`Baixar ${artifact.name}`]])
  })

  it('keeps the displayed history and draft through a failed refresh and an explicit retry', async () => {
    const actual = await vi.importActual<typeof import('../../hooks/useResource')>('../../hooks/useResource')
    vi.mocked(useResource).mockImplementation(actual.useResource)
    const readHistory = (items: Message[]) => Response.json({ conversation_id: conversationId, revision: 3, messages: items })
    const previous = fetcher.getMockImplementation() as (url: string) => Promise<Response>
    const read = vi.fn<() => Promise<Response>>().mockResolvedValue(readHistory([user, reply]))
    fetcher.mockImplementation((url: string) => url.includes('/messages?') ? read() : previous(url))
    const onCleared = vi.fn()
    delegations = { delegations: [turn], has_more: false, next_offset: null }
    await show(onCleared)
    const delivery = container.querySelector(`button[aria-label="Baixar ${artifact.name}"]`)!
    expect(delivery).not.toBeNull()
    const input = container.querySelector('textarea')!
    await act(async () => {
      input.focus()
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Rascunho')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    let fail!: (reason: unknown) => void
    read.mockClear().mockImplementation(() => new Promise((_, reject) => { fail = reject }))
    delegations = { ...delegations, delegations: [...delegations.delegations, { ...turn, id: 'other', source_message_id: 'other', task: { ...turnTask, id: 'other' } }] }
    await show(onCleared)
    expect(read).toHaveBeenCalledTimes(1)
    expect(container.querySelector('[role="log"]')?.contains(delivery)).toBe(true)
    expect(document.activeElement).toBe(input)
    expect(input.value).toBe('Rascunho')
    const submit = container.querySelector<HTMLButtonElement>('button[type="submit"]')!
    expect(submit.disabled).toBe(true)
    expect([...container.querySelectorAll('button')].find((button) => button.textContent === 'Limpar conversa')?.disabled).toBe(true)
    expect(container.querySelector<HTMLButtonElement>('button[aria-label="Anexar arquivo"]')?.disabled).toBe(true)
    await act(async () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
    expect(calls('/agent-messages')).toHaveLength(0)
    await act(async () => fail(new TypeError('offline')))
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('desatualizado')
    expect(submit.disabled).toBe(true)

    let recover!: (value: Response) => void
    read.mockImplementation(() => new Promise((resolve) => { recover = resolve }))
    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Tentar de novo')!
    await act(async () => retry.click())
    expect(retry.disabled).toBe(true)
    await act(async () => retry.click())
    expect(read).toHaveBeenCalledTimes(2)
    expect(container.querySelector('[role="log"]')?.contains(delivery)).toBe(true)
    expect(submit.disabled).toBe(true)
    await act(async () => recover(readHistory([user, reply, { ...user, id: 'new-message', content: 'Mensagem atualizada' }])))
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(container.querySelector('[role="log"]')?.contains(delivery)).toBe(true)
    expect(container.textContent).toContain('Mensagem atualizada')
    expect(submit.disabled).toBe(false)
  })

  it('removes the previous history when the server refuses access during a refresh', async () => {
    const actual = await vi.importActual<typeof import('../../hooks/useResource')>('../../hooks/useResource')
    vi.mocked(useResource).mockImplementation(actual.useResource)
    const previous = fetcher.getMockImplementation() as (url: string) => Promise<Response>
    const read = vi.fn()
      .mockResolvedValueOnce(Response.json({ conversation_id: conversationId, revision: 3, messages: [user, reply] }))
      .mockImplementation(async () => Response.json({ error: { code: 'network_error' } }, { status: 403 }))
    fetcher.mockImplementation((url: string) => url.includes('/messages?') ? read() : previous(url))
    delegations = { delegations: [turn], has_more: false, next_offset: null }
    await show()
    expect(container.querySelector(`button[aria-label="Baixar ${artifact.name}"]`)).not.toBeNull()
    delegations = { ...delegations, delegations: [{ ...turn, task: { ...turnTask, status: 'paused' } }] }
    await show()
    expect(read).toHaveBeenCalledTimes(2)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(container.textContent).not.toContain(user.content)
    expect(container.querySelector(`button[aria-label="Baixar ${artifact.name}"]`)).toBeNull()
    expect(container.textContent).not.toContain('desatualizado')
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

  it('attaches a text file to the message and sends its exact version with consent', async () => {
    await show()
    const picker = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['col1,col2\n1,2\n'], 'dados.csv', { type: 'text/csv' })
    Object.defineProperty(picker, 'files', { value: [file], configurable: true })
    await act(async () => { picker.dispatchEvent(new Event('change', { bubbles: true })) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)) })
    expect(container.textContent).toContain('dados.csv')
    const input = container.querySelector('#chat-input') as HTMLTextAreaElement
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Analise'); input.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 5)) })
    const body = JSON.parse((calls('/agent-messages')[0] as [string, RequestInit])[1].body as string)
    expect(body.attachments).toHaveLength(1)
    expect(body.attachments[0]).toMatchObject({ revision: 1 })
    expect(body.attachment_consent).toEqual({ expected_agent_revision: agent.revision })
    expect(container.querySelector('[aria-label^="Remover"]')).toBeNull()
  })

  it('refuses formats the bee cannot read as data', async () => {
    await show()
    const picker = container.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(picker, 'files', { value: [new File(['x'], 'foto.png', { type: 'image/png' })], configurable: true })
    await act(async () => { picker.dispatchEvent(new Event('change', { bubbles: true })) })
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)) })
    expect(container.textContent).not.toContain('foto.png')
    expect(container.querySelector('[role="alert"], .notice, [class*="error"]')).not.toBeNull()
  })

  it.each([false, true])('pastes files from clipboard, including items-only browsers (%s)', async (itemsOnly) => {
    await show()
    const event = transferEvent('paste', [new File(['# Dados'], 'colado.md')], itemsOnly)
    await act(async () => { container.querySelector('#chat-input')!.dispatchEvent(event) })
    await flushUploads()
    expect(event.defaultPrevented).toBe(true)
    expect(container.textContent).toContain('colado.md')
    expect(publishArtifact).toHaveBeenCalledTimes(1)
  })

  it.each(['paste', 'drop'] as const)('accepts PNG screenshots through %s and explains local OCR', async (kind) => {
    await show()
    const bytes = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aB2QAAAAASUVORK5CYII='), (char) => char.charCodeAt(0))
    const event = transferEvent(kind, [new File([bytes], 'print.png', { type: 'image/png' })])
    await act(async () => { container.querySelector('#chat-input')!.dispatchEvent(event) })
    await flushUploads()
    expect(container.textContent).toContain('print.png')
    expect(container.textContent).toContain('OCR local')
    expect(publishArtifact).toHaveBeenCalledTimes(1)
    expect(vi.mocked(publishArtifact).mock.calls[0][0]).toMatchObject({ media_type: 'image/png', size_bytes: bytes.length })
  })

  it('keeps native text paste and text dragging', async () => {
    await show()
    const input = container.querySelector('#chat-input')!
    const paste = transferEvent('paste'); const drop = transferEvent('drop')
    await act(async () => { input.dispatchEvent(paste); input.dispatchEvent(drop) })
    expect(paste.defaultPrevented).toBe(false)
    expect(drop.defaultPrevented).toBe(false)
    expect(publishArtifact).not.toHaveBeenCalled()
  })

  it('accepts drops anywhere in the conversation and keeps the hint across nested drag events', async () => {
    await show()
    const log = container.querySelector('[role="log"]')!
    const file = new File(['a,b\n1,2'], 'arrastado.csv')
    await act(async () => { log.dispatchEvent(transferEvent('dragenter', [file])); log.dispatchEvent(transferEvent('dragenter', [file])) })
    expect(container.textContent).toContain('Solte os arquivos')
    await act(async () => { log.dispatchEvent(transferEvent('dragleave', [file])) })
    expect(container.textContent).toContain('Solte os arquivos')
    const drop = transferEvent('drop', [file])
    await act(async () => { log.dispatchEvent(drop) })
    await flushUploads()
    expect(drop.defaultPrevented).toBe(true)
    expect(container.textContent).not.toContain('Solte os arquivos')
    expect(container.textContent).toContain('arrastado.csv')
  })

  it('serializes rapid paste/drop batches and enforces the total count', async () => {
    await show()
    const log = container.querySelector('[role="log"]')!
    await act(async () => {
      for (let index = 0; index < 5; index += 1) log.dispatchEvent(transferEvent(index % 2 ? 'paste' : 'drop', [new File(['abc'], `arquivo${index}.txt`)]))
    })
    await flushUploads()
    expect(publishArtifact).toHaveBeenCalledTimes(4)
    expect(container.querySelectorAll('[aria-label^="Remover"]')).toHaveLength(4)
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('4 arquivos ou 64 KB')
  })

  it('keeps successful uploads if another file in the batch fails and enforces byte limits', async () => {
    await show()
    const event = transferEvent('drop', [new File(['ok'], 'primeiro.txt'), new File(['x'], 'foto.png'), new File(['x'.repeat(65536)], 'grande.txt')])
    await act(async () => { container.querySelector('[role="log"]')!.dispatchEvent(event) })
    await flushUploads()
    expect(container.querySelectorAll('[aria-label^="Remover"]')).toHaveLength(1)
    expect(container.textContent).toContain('primeiro.txt')
    expect(publishArtifact).toHaveBeenCalledTimes(1)
  })

  it('blocks Enter while an upload is pending and sends all versions once it finishes', async () => {
    await show()
    let finish!: () => void
    const original = vi.mocked(publishArtifact).getMockImplementation()!
    vi.mocked(publishArtifact).mockImplementationOnce(async (...args) => {
      await new Promise<void>((resolve) => { finish = resolve })
      return original(...args)
    })
    const input = container.querySelector('#chat-input') as HTMLTextAreaElement
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Analise os arquivos')
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(transferEvent('paste', [new File(['a'], 'um.txt')]))
    })
    await flushUploads()
    await act(async () => {
      input.dispatchEvent(transferEvent('paste', [new File(['b'], 'dois.txt')]))
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    })
    expect(calls('/agent-messages')).toHaveLength(0)
    await act(async () => { finish() }); await flushUploads()
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) })
    const body = JSON.parse((calls('/agent-messages')[0] as [string, RequestInit])[1].body as string)
    expect(body.attachments).toHaveLength(2)
    expect(new Set(body.attachments.map((file: { artifact_id: string }) => file.artifact_id)).size).toBe(2)
  })

  it('prevents file navigation without uploading when attachments are unavailable', async () => {
    capabilities = { ...capabilities, available: false }
    await show()
    const drop = transferEvent('drop', [new File(['abc'], 'arquivo.txt')])
    await act(async () => { container.querySelector('[role="log"]')!.dispatchEvent(drop) })
    expect(drop.defaultPrevented).toBe(true)
    expect(publishArtifact).not.toHaveBeenCalled()
  })

  it('shows the provider diagnostic for an immediate send error', async () => {
    await show()
    fetcher.mockImplementation(async () => Response.json({ error: { code: 'provider_rejected', message: 'PRIVATE', diagnostic: { http_status: 400, parameter: 'store', provider_message: 'Unsupported parameter: store' } } }, { status: 502 }))
    const input = container.querySelector('#chat-input') as HTMLTextAreaElement
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Oi'); input.dispatchEvent(new Event('input', { bubbles: true })) })
    await act(async () => { input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })) })
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Unsupported parameter: store')
    expect(container.textContent).toContain('HTTP 400')
    expect(container.textContent).not.toContain('PRIVATE')
  })
})


it('clears only after confirmation, bound to the displayed conversation and revision', async () => {
  messages = [user, reply]
  const cleared = vi.fn(); const confirm = vi.fn().mockReturnValue(false); vi.stubGlobal('confirm', confirm)
  fetcher.mockImplementation(async (url: string) => url.endsWith('/clear') ? Response.json({ conversation_id: '99999999-9999-4999-8999-999999999999' }) : Response.json(capabilities))
  await show(cleared)
  const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('Limpar conversa'))!
  await act(async () => button.click())
  expect(calls('/clear')).toHaveLength(0); expect(cleared).not.toHaveBeenCalled()
  expect(confirm.mock.calls[0][0]).toContain('Memórias, configurações, tarefas e entregas serão preservadas')
  confirm.mockReturnValue(true)
  await act(async () => button.click())
  expect(calls('/clear')).toHaveLength(1); expect(cleared).toHaveBeenCalledOnce()
  const [path, options] = calls('/clear')[0] as [string, RequestInit]
  expect(path).toBe(`/api/v1/agents/${agentId}/conversations/${conversationId}/clear`)
  expect(JSON.parse(options.body as string)).toMatchObject({ consent: 'clear_conversation', expected_revision: agent.revision, expected_conversation_revision: 3 })
  confirm.mockRestore()
})

it('keeps the conversation and explains why pending work prevents clearing', async () => {
  messages = [user, reply]
  const cleared = vi.fn(); const confirm = vi.fn().mockReturnValue(true); vi.stubGlobal('confirm', confirm)
  fetcher.mockImplementation(async (url: string) => url.endsWith('/clear') ? Response.json({ error: { code: 'conversation_has_pending_work' } }, { status: 409 }) : Response.json(capabilities))
  await show(cleared)
  const button = Array.from(container.querySelectorAll('button')).find((item) => item.textContent?.includes('Limpar conversa'))!
  await act(async () => button.click())
  expect(cleared).not.toHaveBeenCalled(); expect(container.textContent).toContain(user.content)
  expect(container.textContent).toContain('Abra Atividade')
  confirm.mockRestore()
})

for (const mode of ['agent', 'chat'] as const) describe(`pending ${mode} message`, () => {
  let finish: (response: Response) => void
  let fail: (error: Error) => void
  let pending: Promise<Response>
  const endpoint = mode === 'agent' ? '/agent-messages' : '/chat'
  async function type(text: string) {
    const input = container.querySelector('#chat-input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, text)
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  async function prepare() {
    if (mode === 'chat') capabilities = { ...capabilities, available: false, tool_calls_supported: false }
    pending = new Promise<Response>((resolve, reject) => { finish = resolve; fail = reject })
    const previous = fetcher.getMockImplementation() as (...args: unknown[]) => Promise<Response>
    fetcher.mockImplementation((...args: unknown[]) => String(args[0]).includes(endpoint) ? pending : previous(...args))
    await show(); await type('Primeira mensagem')
  }
  function send() { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) }
  const receipt = () => Response.json({ task_id: taskId, message_id: 'm', status: 'queued' })

  it('serializes form and Enter submissions in the same turn', async () => {
    await prepare()
    await act(async () => {
      send()
      container.querySelector('#chat-input')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
      send()
    })
    expect(calls(endpoint)).toHaveLength(1)
    await act(async () => finish(receipt()))
    expect((container.querySelector('#chat-input') as HTMLTextAreaElement).value).toBe('')
  })
  it('preserves text typed while the original message is pending', async () => {
    await prepare(); await act(async () => send()); await type('Próxima mensagem')
    await act(async () => finish(receipt()))
    expect((container.querySelector('#chat-input') as HTMLTextAreaElement).value).toBe('Próxima mensagem')
    expect(calls(endpoint)).toHaveLength(1)
    expect(JSON.parse(calls(endpoint)[0][1].body)).toMatchObject({ content: 'Primeira mensagem' })
    await act(async () => send())
    expect(calls(endpoint)).toHaveLength(2)
    expect(JSON.parse(calls(endpoint)[1][1].body)).toMatchObject({ content: 'Próxima mensagem' })
  })
  it('retains the draft and request identity on failure until human retry', async () => {
    await prepare(); await act(async () => send()); await act(async () => fail(new TypeError('offline')))
    expect(calls(endpoint)).toHaveLength(1)
    expect((container.querySelector('#chat-input') as HTMLTextAreaElement).value).toBe('Primeira mensagem')
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    pending = Promise.resolve(receipt()); await act(async () => send())
    expect(calls(endpoint)).toHaveLength(2)
    expect(calls(endpoint)[1][1].body).toBe(calls(endpoint)[0][1].body)
  })
  for (const outcome of ['success', 'failure'] as const) it(`aborts on leaving and ignores late ${outcome}`, async () => {
    await prepare(); await act(async () => send())
    const signal = calls(endpoint)[0][1].signal as AbortSignal
    const refresh = vi.mocked(useTaskPolling).mock.results.at(-1)!.value.refresh
    const focus = vi.spyOn(container.querySelector('#chat-input') as HTMLTextAreaElement, 'focus')
    reload.mockClear(); refresh.mockClear()
    await act(async () => root.unmount()); root = createRoot(container)
    expect(signal?.aborted).toBe(true)
    await act(async () => { if (outcome === 'success') finish(receipt()); else fail(new TypeError('offline')) })
    expect(reload).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled(); expect(focus).not.toHaveBeenCalled()
    expect(calls(endpoint)).toHaveLength(1)
  })
})


describe('conversation clear exclusivity', () => {
  let confirmations: ConfirmRequest[]
  let cleared: ReturnType<typeof vi.fn<() => void>>
  const clearButton = () => [...container.querySelectorAll('button')].find((item) => /Limpar conversa|Limpando/.test(item.textContent ?? ''))!
  const send = () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  async function prepare() {
    messages = [user, reply]; confirmations = []; cleared = vi.fn()
    registerConfirmHost((request) => { confirmations.push(request) })
    await show(cleared)
    const input = container.querySelector('#chat-input')!
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(input, 'Rascunho')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
  }
  for (const action of ['clear', 'send', 'paste', 'drop'] as const) it(`blocks concurrent ${action} before React commits the confirmation state`, async () => {
    await prepare()
    await act(async () => {
      clearButton().click()
      if (action === 'clear') clearButton().click()
      else if (action === 'send') send()
      else container.querySelector('#chat-input')!.dispatchEvent(transferEvent(action, [new File(['a'], 'um.txt')]))
    })
    await flushUploads()
    expect(confirmations).toHaveLength(1)
    expect(calls('/agent-messages')).toHaveLength(0)
    expect(publishArtifact).not.toHaveBeenCalled()
    expect(calls('/clear')).toHaveLength(0)
    await act(async () => confirmations[0].resolve(false))
    expect((container.querySelector('#chat-input') as HTMLTextAreaElement).value).toBe('Rascunho')
    await act(async () => send())
    expect(calls('/agent-messages')).toHaveLength(1)
  })
  it('allows another confirmation after cancellation', async () => {
    await prepare()
    await act(async () => clearButton().click())
    await act(async () => confirmations[0].resolve(false))
    await act(async () => clearButton().click())
    expect(confirmations).toHaveLength(2)
    await act(async () => confirmations[1].resolve(false))
  })
  it('releases the lock after a definitive clear failure', async () => {
    await prepare()
    const previous = fetcher.getMockImplementation() as (...args: unknown[]) => Promise<Response>
    fetcher.mockImplementation((...args) => String(args[0]).endsWith('/clear') ? Promise.resolve(Response.json({ error: { code: 'conversation_has_pending_work' } }, { status: 409 })) : previous(...args))
    await act(async () => clearButton().click())
    await act(async () => confirmations[0].resolve(true))
    expect(cleared).not.toHaveBeenCalled()
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => clearButton().click())
    expect(confirmations).toHaveLength(2)
    await act(async () => confirmations[1].resolve(false))
  })
  it('ignores confirmation resolved after leaving', async () => {
    await prepare()
    await act(async () => clearButton().click())
    await act(async () => root.unmount()); root = createRoot(container)
    await act(async () => confirmations[0].resolve(true))
    expect(calls('/clear')).toHaveLength(0)
    expect(cleared).not.toHaveBeenCalled()
  })
  for (const outcome of ['success', 'failure'] as const) it(`ignores late clear ${outcome} after leaving`, async () => {
    await prepare()
    let finish!: (response: Response) => void
    let fail!: (error: Error) => void
    const pending = new Promise<Response>((resolve, reject) => { finish = resolve; fail = reject })
    const previous = fetcher.getMockImplementation() as (...args: unknown[]) => Promise<Response>
    fetcher.mockImplementation((...args) => String(args[0]).endsWith('/clear') ? pending : previous(...args))
    await act(async () => clearButton().click())
    await act(async () => confirmations[0].resolve(true))
    const signal = calls('/clear')[0][1].signal as AbortSignal
    const refresh = vi.mocked(useTaskPolling).mock.results.at(-1)!.value.refresh
    reload.mockClear(); refresh.mockClear()
    await act(async () => root.unmount()); root = createRoot(container)
    expect(signal.aborted).toBe(true)
    await act(async () => {
      if (outcome === 'success') finish(Response.json({ conversation_id: '99999999-9999-4999-8999-999999999999' }))
      else fail(new TypeError('offline'))
    })
    expect(cleared).not.toHaveBeenCalled()
    expect(reload).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled()
  })
})
