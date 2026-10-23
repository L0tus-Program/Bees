import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { agentResources } from '../../locales/agent'
import { productResources } from '../../locales/product'
import { taskResources } from '../../locales/tasks'
import { DelegationCard } from '../chat/DelegationCard'
import { task } from '../chat/testFixtures'
import { AgentSteps } from './AgentSteps'
import { decideAgentApproval, getAgentCapabilities, sendAgentMessage } from '../../api/agent'

vi.mock('./AgentApprovals', () => ({ AgentApprovals: () => <p>approvals-panel</p> }))

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const instance = createInstance()
  await instance.init({ lng: language, resources: { [language]: { agent: agentResources[language], product: productResources[language], tasks: taskResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={instance}>{content}</I18nextProvider>)
}

const agentTask = { ...task, execution_kind: 'agent_task', agent_chat_conversation_id: 'chat-1', agent_steps: [
  { tool: 'web_search', status: 'confirmed', code: null, at: '2026-10-23T10:00:00Z' },
  { tool: 'web_fetch', status: 'failed', code: 'web_timeout', at: '2026-10-23T10:00:05Z' },
] }

describe('agent task in chat', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('shows translated steps in %s', async (language) => {
    const html = await render(<AgentSteps task={agentTask} />, language)
    expect(html).toContain(agentResources[language].functions.web_search)
    expect(html).toContain(agentResources[language].stepStatus.failed)
    expect(html).toContain('web_timeout')
  })

  it('does not duplicate the chat reply inside the agent card and shows pending decisions', async () => {
    const completed = await render(<DelegationCard task={agentTask} selected={false} onSelect={() => {}} />)
    expect(completed).toContain(agentResources['pt-BR'].agentTask)
    expect(completed).not.toContain(task.latest_run!.result!.content.split('\n')[0])
    const waiting = await render(<DelegationCard task={{ ...agentTask, status: 'waiting_approval' }} selected={false} onSelect={() => {}} />)
    expect(waiting).toContain('approvals-panel')
    const running = await render(<DelegationCard task={{ ...agentTask, status: 'running' }} selected={false} onSelect={() => {}} />)
    expect(running).toContain(agentResources['pt-BR'].working)
  })
})

describe('agent api client', () => {
  it('sends the chat message with the same request identity and validates responses', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ task_id: 't1', message_id: 'm1', status: 'queued' }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const { setCsrfToken } = await import('../../api/client')
    setCsrfToken('csrf')
    const result = await sendAgentMessage('bee-1', 'chat-1', 'Pesquise abelhas', 'req-1')
    expect(result.task_id).toBe('t1')
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/v1/agents/bee-1/conversations/chat-1/agent-messages')
    expect(JSON.parse(init.body as string)).toEqual({ client_request_id: 'req-1', content: 'Pesquise abelhas' })
  })

  it('rejects malformed capabilities and decision receipts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ available: 'yes' }), { status: 200 })))
    await expect(getAgentCapabilities('bee-1')).rejects.toThrow('invalid_response')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ approval: {}, receipt: { confirmed: true } }), { status: 200 })))
    const approval = { id: 'a1', task_id: 't1', run_id: 'r1', status: 'pending', decision: null, revision: 1, created_at: '2026-10-23T10:00:00Z', expires_at: '2026-10-23T11:00:00Z', consumed_at: null, valid: true, stale_reason: null, tool_name: 'memory.save', effect: 'write', summary: 'x', task_title: 't', target: { kind: 'control_plane', environment_id: 'control_plane' }, arguments_preview: null } as const
    await expect(decideAgentApproval('bee-1', approval, 'allow_once', 'req')).rejects.toThrow('invalid_response')
  })
})
