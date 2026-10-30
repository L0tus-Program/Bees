// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { TaskRecord } from '../../api/tasks'
import { productResources } from '../../locales/product'
import { workspaceResources } from '../../locales/workspace'
import { task as baseTask } from '../chat/testFixtures'
import { ActivityPanel } from './ActivityPanel'

let root: Root, container: HTMLDivElement
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); container = document.createElement('div'); document.body.append(container); root = createRoot(container) })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })

const make = (id: string, title: string, status: TaskRecord['status']): TaskRecord => ({ ...baseTask, id, title, status, updated_at: new Date().toISOString() })

async function show(tasks: TaskRecord[], onOpen = vi.fn()) {
  const i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { workspace: workspaceResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  const activity = { tasks, data: { tasks, has_more: false }, loading: false, error: null, refresh: vi.fn(), active: 0, attention: 0 }
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ActivityPanel activity={activity} onOpen={onOpen} /></I18nextProvider>))
  return onOpen
}

describe('activity panel', () => {
  it('groups tasks by what needs the person, what is running and what finished', async () => {
    const tasks = [make('a', 'Pesquisa', 'running'), make('b', 'Apagar arquivo', 'waiting_approval'), make('c', 'Relatório', 'completed')]
    const onOpen = await show(tasks)
    const headings = [...container.querySelectorAll('h3')].map((item) => item.textContent)
    expect(headings).toEqual(['Precisam de você (1)', 'Em andamento (1)', 'Recentes (1)'])
    const sections = [...container.querySelectorAll('section')].map((item) => item.textContent ?? '')
    expect(sections[0]).toContain('Apagar arquivo'); expect(sections[1]).toContain('Pesquisa'); expect(sections[2]).toContain('Relatório')
    await act(async () => { (container.querySelector('section:last-of-type button') as HTMLButtonElement).click() })
    expect(onOpen).toHaveBeenCalledWith('c')
  })

  it('keeps finished tasks minimized until asked', async () => {
    await show(Array.from({ length: 12 }, (_, index) => make(`t${index}`, `Tarefa ${index}`, 'completed')))
    expect(container.querySelectorAll('li')).toHaveLength(8)
    const more = [...container.querySelectorAll('button')].find((item) => item.textContent?.startsWith('Mostrar todas'))!
    await act(async () => more.click())
    expect(container.querySelectorAll('li')).toHaveLength(12)
  })

  it('explains the empty state', async () => {
    await show([])
    expect(container.textContent).toContain('Nenhuma tarefa ainda')
  })
})
