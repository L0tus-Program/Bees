import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, expect, it, vi } from 'vitest'
import { workspaceResources } from '../../locales/workspace'
import { productResources } from '../../locales/product'
import { agentResources } from '../../locales/agent'
import { taskResources } from '../../locales/tasks'
import { toolTaskResources } from '../../locales/toolTasks'
import { agent, task } from '../chat/testFixtures'
import type { TaskRecord } from '../../api/tasks'
import { providerDiagnostic } from '../../api/client'
import { TaskFailure } from './TaskFailure'
import { TaskPanel } from './TaskPanel'
import { ActionHistory } from './ActionHistory'
import { useTaskPolling } from './useTaskPolling'

vi.mock('./useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
afterEach(() => vi.resetAllMocks())

const failed: TaskRecord = { ...task, status: 'paused', unknown_requires_ack: true, unknown_model_calls: 1,
  latest_run: { ...task.latest_run!, error_code: 'outcome_unknown', failure: { code: 'invalid_response', response_issue: 'tool_arguments_json', http_status: null, parameter: null } } }

async function render(element: React.ReactNode, language: 'pt-BR' | 'en' | 'es') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { workspace: workspaceResources[language], product: productResources[language], agent: agentResources[language], tasks: taskResources[language], toolTasks: toolTaskResources[language] } }, interpolation: { escapeValue: false } })
  return { html: renderToStaticMarkup(<I18nextProvider i18n={i18n}>{element}</I18nextProvider>), i18n }
}

it.each(['pt-BR', 'en', 'es'] as const)('shows the concrete cause directly in the task list in %s', async (language) => {
  vi.mocked(useTaskPolling).mockReturnValue({ data: { tasks: [failed], has_more: false }, loading: false, error: null, refresh: vi.fn() } as never)
  const { html, i18n } = await render(<TaskPanel agent={agent} />, language)
  expect(html).toContain(i18n.t('responseIssue.tool_arguments_json', { ns: 'workspace' }))
  expect(html).toContain('invalid_response')
  expect(html).not.toContain(i18n.t('errors.invalid_response', { ns: 'product' }))
  expect(html).not.toContain('<details')
})

it.each(['pt-BR', 'en', 'es'] as const)('shows known tool causes and codes in history in %s', async (language) => {
  const { html, i18n } = await render(<ActionHistory hasMore={false} actions={[{
    id: 'a', run_id: 'r', tool_name: 'web.fetch', status: 'failed_no_effect', error_code: 'web_private_address_blocked', obsolete: false, acknowledged: false, created_at: task.created_at, updated_at: task.updated_at,
  }]} />, language)
  expect(html).toContain(i18n.t('stepCode.private_address_blocked', { ns: 'agent' }))
  expect(html).toContain('web_private_address_blocked')
})

it('keeps tool uncertainty separate from model failure and recovers recent rejected steps', async () => {
  const toolFailure: TaskRecord = { ...failed, unknown_model_calls: 0, unknown_tool_actions: 1, latest_run: { ...failed.latest_run!, failure: { source: 'tool', tool_name: 'computer.browser.open', code: 'executor_timeout', http_status: null, parameter: null } } }
  const { html } = await render(<TaskFailure task={toolFailure} warnUnknown />, 'pt-BR')
  expect(html).toContain('computer.browser.open')
  expect(html).toContain('executor_timeout')
  expect(html).not.toContain('Modelo usado')
  expect(html).not.toContain('Créditos podem ter sido consumidos')
  const rejected: TaskRecord = { ...failed, latest_run: { ...failed.latest_run!, failure: null, error_code: 'task_limit_reached' }, agent_steps: [{ tool: 'web_fetch', status: 'rejected', code: 'invalid_tool_arguments', at: task.updated_at }] }
  expect((await render(<TaskFailure task={rejected} />, 'pt-BR')).html).toContain('invalid_tool_arguments')
})

it('does not invent a missing structural reason for an older response', async () => {
  const { html } = await render(<TaskFailure task={{ ...failed, latest_run: { ...failed.latest_run!, failure: null, error_code: 'invalid_response' } }} />, 'pt-BR')
  expect(html).toContain('motivo específico não está registrado')
  expect(html).not.toContain('Os argumentos da ferramenta não formam')
  expect(providerDiagnostic({ http_status: null, parameter: null, response_issue: 'PRIVATE EXCEPTION' })).toBeNull()
})
