// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { expect, it, vi } from 'vitest'
import { TaskDetail } from './TaskDetail'
import { useTaskPolling } from './useTaskPolling'
import { skillTaskReceipt } from '../skills/skillTaskFixtures'
import { skillResources } from '../../locales/skills'
import { taskResources } from '../../locales/tasks'
import { agent } from '../chat/testFixtures'

vi.mock('./useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
vi.mock('../approvals/ApprovalPanel', () => ({ ApprovalPanel: () => <div>Approvals</div> }))
vi.mock('../artifacts/TaskArtifacts', () => ({ TaskArtifacts: () => null }))

it('keeps one fixed-context control beside the approval panel across task updates', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const container = document.createElement('div'); document.body.append(container)
  const root = createRoot(container)
  const i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { skills: skillResources['pt-BR'], tasks: taskResources['pt-BR'] } } })
  const task = { ...skillTaskReceipt().task, tool_approvals_available: false }
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  try {
    for (let revision = 1; revision <= 5; revision++) {
      vi.mocked(useTaskPolling).mockReturnValue({ data: { task: { ...task, revision }, events: [], actions: [], has_more: false }, loading: false, error: null, refresh: vi.fn() })
      await act(async () => root.render(<I18nextProvider i18n={i18n}><TaskDetail agent={{ ...agent, id: task.agent_id }} taskId={task.id} onClose={() => {}} onRefresh={() => {}} /></I18nextProvider>))
      expect(container.querySelectorAll('.skill-task-context')).toHaveLength(1)
      expect([...container.querySelectorAll('button')].filter((button) => button.textContent === skillResources['pt-BR'].runRead)).toHaveLength(1)
    }
    expect(fetcher).not.toHaveBeenCalled()
  } finally { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks() }
})
