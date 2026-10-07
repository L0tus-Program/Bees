import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import type { AgentSummary } from '../../api/onboarding'
import type { TaskRecord } from '../../api/tasks'
import { approvalResources } from '../../locales/approvals'
import { taskResources } from '../../locales/tasks'
import { TaskDetail } from './TaskDetail'
import { TaskForm } from './TaskForm'
import { useTaskPolling } from './useTaskPolling'

vi.mock('./useTaskPolling', () => ({ useTaskPolling: vi.fn() }))

const stamp = '2026-10-07T18:00:00Z'
const agent: AgentSummary = { id: 'bee-1', name: 'Abelha', purpose: 'Organizar ideias', instructions: '', provider_config: null, conversation_id: 'conversation-1', revision: 1, status: 'active', memory_enabled: true }
const task: TaskRecord = {
  id: 'task-1', agent_id: agent.id, conversation_id: 'task-conversation', title: 'Redigir redação sobre o acesso à cultura no Brasil',
  objective: 'Redação de qualidade', expected_result: 'Uma ótima redação', status: 'completed', revision: 3,
  created_at: stamp, updated_at: stamp, active_run_id: 'run-1', control_requested: null, available_controls: [],
  latest_run: { id: 'run-1', status: 'completed', provider: 'openai_compatible', model: 'model-1', started_at: stamp, finished_at: stamp, error_code: null, result: { content: 'O acesso à cultura no Brasil enfrenta barreiras econômicas e regionais.\n\nAmpliar a oferta pública ajuda a promover a participação cultural.', created_at: stamp } },
  progress: { code: 'completed', created_at: stamp }, unknown_requires_ack: false, unknown_model_calls: 0, unknown_tool_actions: 0, action_in_flight: false,
}

afterEach(() => vi.resetAllMocks())

async function renderTask(record: TaskRecord = task, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  vi.mocked(useTaskPolling)
    .mockReturnValueOnce({ data: { task: record, events: [], actions: [], has_more: false }, loading: false, error: null, refresh: vi.fn() })
    .mockReturnValueOnce({ data: { approvals: [], has_more: false, next_offset: null }, loading: false, error: null, refresh: vi.fn() })
  return render(<TaskDetail agent={agent} taskId={record.id} onRefresh={() => {}} onClose={() => {}} />, language)
}

async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { tasks: taskResources[language], approvals: approvalResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}

describe('task response presentation', () => {
  it('shows the actual answer immediately after completed status, before instructions and even an empty decision panel', async () => {
    const html = await renderTask()
    expect(html).toContain(task.latest_run!.result!.content)
    const response = html.indexOf('Resposta da tarefa')
    expect(response).toBeGreaterThan(html.indexOf('Resposta pronta'))
    expect(response).toBeLessThan(html.indexOf('<dt>Objetivo</dt>'))
    expect(response).toBeLessThan(html.indexOf('Decisões da abelha'))
    expect(html).toContain('Nenhuma decisão registrada ainda.')
    expect(html).not.toContain('Resultado recuperado')
  })

  it('retains the unknown outcome warning and recovery controls without inventing an answer', async () => {
    const paused: TaskRecord = { ...task, status: 'paused', available_controls: ['resume', 'cancel'], unknown_model_calls: 1, unknown_requires_ack: true, latest_run: { ...task.latest_run!, status: 'paused', error_code: 'outcome_unknown', result: null } }
    const html = await renderTask(paused)
    expect(html).toContain('O resultado da chamada anterior é desconhecido')
    expect(html).toContain('Nenhuma retomada ocorre automaticamente')
    expect(html).toContain('Retomar</button>')
    expect(html).toContain('Cancelar</button>')
    expect(html).not.toContain('task-result-heading')
    expect(html).not.toContain(task.latest_run!.result!.content)
    expect(html).not.toContain('A tarefa está concluída, mas')
  })

  it('explicitly explains a completed task whose answer is missing from the current query', async () => {
    const html = await renderTask({ ...task, latest_run: { ...task.latest_run!, result: null } })
    expect(html).toContain('A tarefa está concluída, mas a resposta não está disponível nesta consulta')
    expect(html).toContain('Atualize as tarefas para conferir')
    expect(html).not.toContain('task-result-heading')
  })

  it('escapes provider content instead of interpreting it as HTML', async () => {
    const html = await renderTask({ ...task, latest_run: { ...task.latest_run!, result: { content: '<script>alert("unsafe")</script>', created_at: stamp } } })
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
  })

  it.each(['en', 'es'] as const)('names the response clearly in %s', async (language) => {
    const html = await renderTask(task, language)
    expect(html).toContain(language === 'en' ? 'Task response' : 'Respuesta de la tarea')
    expect(html).not.toContain(language === 'en' ? 'Recovered result' : 'Resultado recuperado')
  })

  it.each(['pt-BR', 'en', 'es'] as const)('explains request and requirements without adding form fields in %s', async (language) => {
    const html = await render(<TaskForm agent={agent} onCreated={() => {}} onClose={() => {}} onRefresh={() => {}} />, language)
    expect(html).toContain(taskResources[language].titleHelp)
    expect(html).toContain(taskResources[language].objectiveHelp)
    expect(html).toContain('aria-describedby="task-title-help"')
    expect(html).toContain('aria-describedby="task-objective-help"')
    expect(html.match(/<input /g)).toHaveLength(1)
    expect(html.match(/<textarea /g)).toHaveLength(2)
  })
})
