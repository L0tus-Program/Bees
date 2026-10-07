import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import type { Message } from '../../api/onboarding'
import { productResources } from '../../locales/product'
import { taskResources } from '../../locales/tasks'
import { approvalResources } from '../../locales/approvals'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { useResource } from '../../hooks/useResource'
import { Chat } from './Chat'
import { ChatHistory } from './ChatHistory'
import { DelegationCard } from './DelegationCard'
import { agent, delegation, stamp, task } from './testFixtures'

vi.mock('../tasks/useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
vi.mock('../../hooks/useResource', () => ({ useResource: vi.fn() }))
vi.mock('../approvals/ApprovalPanel', () => ({ ApprovalPanel: () => null }))

afterEach(() => vi.resetAllMocks())

async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const instance = createInstance()
  await instance.init({ lng: language, resources: { [language]: { product: productResources[language], tasks: taskResources[language], approvals: approvalResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={instance}>{content}</I18nextProvider>)
}
function project(items = [delegation]) {
  vi.mocked(useTaskPolling).mockReturnValue({ data: { delegations: items, has_more: false, next_offset: null }, loading: false, error: null, refresh: vi.fn() })
}
const history: Message[] = [
  { id: 'message-user', role: 'user', content: 'Faça uma redação sobre cultura', created_at: stamp },
  { id: 'message-assistant', role: 'assistant', content: null, created_at: stamp },
  { id: 'message-tool', role: 'tool', content: '{"status":"created","task_id":"task-1"}', created_at: stamp },
]

describe('delegation in chat', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('offers unchecked consent and concrete limits in %s', async (language) => {
    project([])
    vi.mocked(useResource).mockReturnValue({ resource: { state: 'ready', data: { conversation_id: agent.conversation_id, messages: [] } }, reload: vi.fn() })
    const html = await render(<Chat agent={agent} />, language)
    expect(html).toContain(productResources[language].chatAllowDelegation)
    expect(html).toContain(productResources[language].chatAllowDelegationHelp)
    expect(html).toContain('type="checkbox"')
    expect(html).not.toContain('checked=""')
  })

  it('keeps ordinary chat available for models without tool calls and points to manual tasks', async () => {
    project([])
    vi.mocked(useResource).mockReturnValue({ resource: { state: 'ready', data: { conversation_id: agent.conversation_id, messages: [] } }, reload: vi.fn() })
    const html = await render(<Chat agent={{ ...agent, provider_config: { ...agent.provider_config!, capabilities: { text: true, tool_calls: false } } }} />)
    expect(html).toContain(productResources['pt-BR'].chatDelegationUnavailable)
    expect(html).toContain('id="chat-input"')
    expect(html).not.toContain('type="checkbox"')
  })

  it('places a recovered canonical response at its assistant turn without displaying raw tool JSON or misleading no-text notices', async () => {
    project()
    const html = await render(<ChatHistory agent={agent} messages={history} refreshVersion={0} />)
    expect(html).toContain(task.latest_run!.result!.content)
    expect(html.indexOf(task.title)).toBeGreaterThan(html.indexOf(history[0].content!))
    expect(html).not.toContain('&quot;task_id&quot;')
    expect(html).not.toContain(productResources['pt-BR'].noTextContent)
    expect(html.match(/Resposta da tarefa/g)).toHaveLength(1)
  })

  it('recovers a task even when its source message is outside the recent-history window', async () => {
    project()
    const html = await render(<ChatHistory agent={agent} messages={[]} refreshVersion={0} />)
    expect(html).toContain(productResources['pt-BR'].chatDelegationRecovered)
    expect(html).toContain(task.latest_run!.result!.content)
  })

  it('shows a concrete durable refusal instead of trusting the model claim that it created a task', async () => {
    project([])
    const html = await render(<ChatHistory agent={agent} messages={[{ ...history[1], content: 'Criei sua tarefa com sucesso!', delegation_results: [{ tool_call_id: 'call-1', status: 'rejected', code: 'pending_task_limit', task_id: null }] }]} refreshVersion={0} />)
    expect(html).toContain(productResources['pt-BR'].chatDelegationRejectedLimit)
    expect(html).not.toContain('Criei sua tarefa com sucesso!')
    expect(html).not.toContain('delegation-card')
  })

  it('escapes the entire result and avoids duplicating it while task controls are open', async () => {
    const unsafe = { ...task, latest_run: { ...task.latest_run!, result: { content: '<script>alert("x")</script>\n\nSegundo parágrafo.', created_at: stamp } } }
    let html = await render(<DelegationCard task={unsafe} selected={false} onSelect={() => {}} />)
    expect(html).toContain('&lt;script&gt;')
    expect(html).toContain('Segundo parágrafo.')
    expect(html).not.toContain('<script>')
    html = await render(<DelegationCard task={unsafe} selected onSelect={() => {}} />)
    expect(html).not.toContain('Segundo parágrafo.')
    expect(html).toContain(productResources['pt-BR'].chatDelegationClose)
  })

  it('prioritizes uncertainty and makes waiting approval and missing output explicit', async () => {
    const paused = { ...task, status: 'paused' as const, unknown_requires_ack: true, unknown_model_calls: 1, latest_run: { ...task.latest_run!, result: null, error_code: 'outcome_unknown' } }
    let html = await render(<DelegationCard task={paused} selected={false} onSelect={() => {}} />)
    expect(html).toContain(taskResources['pt-BR'].unknownWarning)
    expect(html).not.toContain(task.latest_run!.result!.content)
    html = await render(<DelegationCard task={{ ...paused, status: 'waiting_approval', unknown_requires_ack: false, unknown_model_calls: 0 }} selected={false} onSelect={() => {}} />)
    expect(html).toContain(productResources['pt-BR'].chatDelegationApproval)
    html = await render(<DelegationCard task={{ ...task, latest_run: { ...task.latest_run!, result: null } }} selected={false} onSelect={() => {}} />)
    expect(html).toContain(taskResources['pt-BR'].resultUnavailable)
  })
})
