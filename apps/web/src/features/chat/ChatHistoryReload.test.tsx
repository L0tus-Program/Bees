// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { DelegationCollection } from '../../api/delegations'
import { productResources } from '../../locales/product'
import { taskResources } from '../../locales/tasks'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { ChatHistory } from './ChatHistory'
import { agent, delegation } from './testFixtures'

vi.mock('../tasks/useTaskPolling', () => ({ useTaskPolling: vi.fn() }))

let root: Root, container: HTMLDivElement
let data: DelegationCollection | null = null
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  vi.mocked(useTaskPolling).mockImplementation(() => ({ data, loading: data === null, error: null, refresh: vi.fn() }) as never)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks(); data = null })

const done = (id: string) => ({ ...delegation, id, task: { ...delegation.task, id: `task-${id}`, execution_kind: 'agent_task', status: 'completed' } })
const collection = (...items: ReturnType<typeof done>[]) => ({ delegations: items, has_more: false, next_offset: null }) as DelegationCollection

async function show(key: string, onAgentResult: () => void) {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { product: productResources['pt-BR'], tasks: taskResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ChatHistory key={key} agent={agent} messages={[]} refreshVersion={0} onAgentResult={onAgentResult} /></I18nextProvider>))
}

it('does not reload the conversation for an agent result that already existed, even after remounting', async () => {
  const onAgentResult = vi.fn()
  await show('a', onAgentResult)
  data = collection(done('1'))
  await show('a', onAgentResult)
  // Remontar (como ao recarregar a conversa) começa sem dados e relê a mesma conclusão.
  data = null
  await show('b', onAgentResult)
  data = collection(done('1'))
  await show('b', onAgentResult)
  expect(onAgentResult).not.toHaveBeenCalled()
  data = collection(done('1'), done('2'))
  await show('b', onAgentResult)
  expect(onAgentResult).toHaveBeenCalledTimes(1)
})
