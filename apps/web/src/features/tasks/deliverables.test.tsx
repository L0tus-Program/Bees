// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { getTasks } from '../../api/tasks'
import type { DeliverableItem, TaskRecord } from '../../api/tasks'
import type { AgentSummary } from '../../api/onboarding'
import { productResources } from '../../locales/product'
import { taskResources } from '../../locales/tasks'
import { TaskDeliverables } from './TaskDeliverables'
import { TaskForm } from './TaskForm'

const stamp = '2026-10-16T12:00:00Z'
const hash = 'a'.repeat(64)
const agent: AgentSummary = { id: 'bee-1', name: 'Abelha', purpose: 'Organizar', instructions: '', provider_config: null, conversation_id: null, revision: 1, status: 'active', memory_enabled: true }
const report: DeliverableItem = { kind: 'report', status: 'ready', error_code: null, artifact_id: 'artifact-1', sha256: hash, size_bytes: 40, version: 1, rows: null, columns: null, formula_like_cells: null }
const table: DeliverableItem = { kind: 'table', status: 'ready', error_code: null, artifact_id: 'artifact-2', sha256: hash, size_bytes: 30, version: 1, rows: 12, columns: 3, formula_like_cells: 2 }
const task: TaskRecord = {
  id: 'task-1', agent_id: agent.id, conversation_id: 'conversation-1', title: 'Plano', objective: 'Objetivo', expected_result: 'Resultado', status: 'completed', revision: 4,
  created_at: stamp, updated_at: stamp, active_run_id: 'run-1', control_requested: null, available_controls: [], latest_run: null, progress: null,
  unknown_requires_ack: false, unknown_model_calls: 0, unknown_tool_actions: 0, action_in_flight: false,
  deliverables: ['report', 'table'], deliverable_results: { items: [report, table], partial: false, missing_data: [] }, publication_pending: false,
}

async function instance(language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { tasks: taskResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  return i18n
}
async function staticHtml(node: ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  return renderToStaticMarkup(<I18nextProvider i18n={await instance(language)}>{node}</I18nextProvider>)
}

describe('delivery files presentation', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('shows verified files, table shape and formula warning in %s', async (language) => {
    const html = await staticHtml(<TaskDeliverables task={task} />, language)
    const text = taskResources[language].deliverables
    expect(html).toContain(text.kind.report); expect(html).toContain(text.kind.table); expect(html).toContain(text.status.ready)
    expect(html).toContain(text.tableShape.replace('{{rows}}', '12').replace('{{columns}}', '3'))
    expect(html).toContain(text.formulas.replace('{{total}}', '2'))
    expect(html).toContain(text.scope)
    expect(html).not.toContain(text.partial)
  })
  it('marks partial delivery, explains invalid files and escapes reported missing data', async () => {
    const invalid: DeliverableItem = { ...table, status: 'invalid', error_code: 'table_columns_inconsistent', artifact_id: null, sha256: null, size_bytes: null, version: null, rows: null, columns: null, formula_like_cells: null }
    const html = await staticHtml(<TaskDeliverables task={{ ...task, status: 'paused', deliverable_results: { items: [report, invalid], partial: true, missing_data: ['<b>Série 2026</b>'] } }} />)
    expect(html).toContain('Entrega parcial'); expect(html).toContain('&lt;b&gt;Série 2026&lt;/b&gt;'); expect(html).not.toContain('<b>Série')
    expect(html).toContain('Há linhas com número de colunas diferente do cabeçalho')
    expect(html).toContain('formato inválido')
  })
  it('explains pending publication without promising a new generation and hides the section without requested files', async () => {
    const pending = await staticHtml(<TaskDeliverables task={{ ...task, status: 'paused', deliverable_results: null, publication_pending: true }} />)
    expect(pending).toContain('sem nova chamada ao modelo')
    const awaiting = await staticHtml(<TaskDeliverables task={{ ...task, status: 'queued', deliverable_results: null }} />)
    expect(awaiting).toContain('aguardando a resposta da tarefa')
    expect(await staticHtml(<TaskDeliverables task={{ ...task, deliverables: [] }} />)).toBe('')
    expect(await staticHtml(<TaskDeliverables task={{ ...task, deliverables: undefined }} />)).toBe('')
  })
})

describe('delivery files contract', () => {
  afterEach(() => vi.unstubAllGlobals())
  it.each([
    { ...task, deliverables: ['docx'] }, { ...task, deliverables: ['report', 'report'] },
    { ...task, deliverable_results: { items: [{ ...report, sha256: null }], partial: false, missing_data: [] } },
    { ...task, deliverable_results: { items: [{ ...report, status: 'invalid' }], partial: false, missing_data: [] } },
    { ...task, deliverable_results: { items: [report, report], partial: false, missing_data: [] } },
    { ...task, deliverable_results: { items: [{ ...report, status: 'done' }], partial: false, missing_data: [] } },
    { ...task, deliverable_results: { items: [report], partial: 'yes', missing_data: [] } },
    { ...task, deliverable_results: { items: [report], partial: true, missing_data: [1] } },
    { ...task, publication_pending: 'true' },
  ])('refuses malformed delivery state: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ tasks: [value], has_more: false })))
    await expect(getTasks(agent.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})

describe('choosing delivery files when delegating', () => {
  let root: Root
  let container: HTMLDivElement
  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('test-csrf')
    container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  })
  afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); setCsrfToken() })
  function type(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), 'value')!.set!.call(element, value)
    element.dispatchEvent(new Event('input', { bubbles: true }))
  }
  async function submit(choose: string[]) {
    const fetcher = vi.fn(async () => Response.json({ ...task, status: 'queued', deliverable_results: null }, { status: 201 }))
    vi.stubGlobal('fetch', fetcher)
    const onCreated = vi.fn()
    const i18n = await instance()
    await act(async () => root.render(<I18nextProvider i18n={i18n}><TaskForm agent={agent} onCreated={onCreated} onClose={() => {}} onRefresh={() => {}} /></I18nextProvider>))
    await act(async () => {
      type(container.querySelector('#task-title') as HTMLInputElement, 'Plano')
      type(container.querySelector('#task-objective') as HTMLTextAreaElement, 'Comparar opções')
      type(container.querySelector('#task-expected') as HTMLTextAreaElement, 'Tabela comparativa')
    })
    for (const kind of choose) await act(async () => (container.querySelector(`#task-deliverable-${kind}`) as HTMLInputElement).click())
    await act(async () => (container.querySelector('button[type=submit]') as HTMLButtonElement).click())
    for (let i = 0; i < 50 && !onCreated.mock.calls.length; i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2)) })
    expect(onCreated).toHaveBeenCalledTimes(1)
    return JSON.parse((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body as string)
  }
  it('starts with no files selected and sends the chosen formats in canonical order', async () => {
    const body = await submit(['table', 'report'])
    expect(body.deliverables).toEqual(['report', 'table'])
  })
  it('keeps the previous request body when no file is chosen', async () => {
    const body = await submit([])
    expect(body).not.toHaveProperty('deliverables')
    expect(Object.keys(body).sort()).toEqual(['client_request_id', 'expected_result', 'objective', 'title'])
  })
})
