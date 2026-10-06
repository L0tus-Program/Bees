import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { setCsrfToken } from '../../api/client'
import { controlTask, getTask, getTasks } from '../../api/tasks'
import type { ActionSummary, TaskRecord } from '../../api/tasks'
import { taskResources } from '../../locales/tasks'
import { ActionHistory } from './ActionHistory'
import { UnknownAcknowledgment } from './UnknownAcknowledgment'
import { commandIdentity, taskAcknowledgmentKey, taskControlBlocked, taskOutcomeUnknown, taskUnknownKind } from './state'

const stamp = '2026-10-06T18:00:00Z'
const task: TaskRecord = { id: 'task-1', agent_id: 'bee-1', conversation_id: 'conversation-1', title: 'Resposta de teste', objective: 'Objetivo', expected_result: 'Resultado',
  status: 'paused', revision: 5, created_at: stamp, updated_at: stamp, active_run_id: 'run-1', control_requested: null, available_controls: ['resume', 'cancel'], latest_run: null, progress: null,
  unknown_requires_ack: true, unknown_model_calls: 0, unknown_tool_actions: 1, action_in_flight: false }
const action: ActionSummary = { id: 'action-1', run_id: 'run-1', tool_name: 'text.normalize', status: 'outcome_unknown', error_code: 'outcome_unknown', obsolete: false, acknowledged: false, created_at: stamp, updated_at: stamp }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { tasks: taskResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('sanitized action observation', () => {
  it('observes a tool journal without returning arguments, results or internal metadata to UI state', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ task, events: [], actions: [{ ...action, parameters: { text: 'private input' }, result: { text: 'private output' }, metadata: { credential_ref: 'private reference' } }], has_more: false }))
    vi.stubGlobal('fetch', fetcher)
    const detail = await getTask('bee-1', task.id)
    expect(detail.actions).toEqual([action]); expect(JSON.stringify(detail)).not.toContain('private')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET' }); expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it('uses global unknown aggregates even when a limited action page is empty', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ task, events: [], actions: [], has_more: true })))
    const detail = await getTask('bee-1', task.id)
    expect(taskOutcomeUnknown(detail.task)).toBe(true); expect(taskUnknownKind(detail.task)).toBe('tool'); expect(detail.actions).toEqual([])
  })
  it.each([
    { ...task, unknown_tool_actions: -1 }, { ...task, unknown_model_calls: 1.5 }, { ...task, unknown_requires_ack: false },
    { ...task, action_in_flight: 'true' }, { ...task, unknown_tool_actions: Number.MAX_SAFE_INTEGER + 1 },
  ])('rejects malformed or inconsistent global recovery state: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ tasks: [value], has_more: false })))
    await expect(getTasks('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { ...action, status: 'invented' }, { ...action, created_at: 'invalid' }, { ...action, tool_name: '' },
    { ...action, status: 'prepared', acknowledged: true }, { ...action, error_code: 'private payload with spaces' }, { ...action, obsolete: 1 },
  ])('rejects malformed action summary fields: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ task, events: [], actions: [value], has_more: false })))
    await expect(getTask('bee-1', task.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects duplicate action records and missing journal completeness', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ task, events: [], actions: [action, action], has_more: false })).mockResolvedValueOnce(Response.json({ task, events: [], actions: [action] }))
    vi.stubGlobal('fetch', fetcher)
    await expect(getTask('bee-1', task.id)).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getTask('bee-1', task.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each(['confirmed', 'failed_no_effect'] as const)('preserves acknowledgment after a proven reconciliation to %s', async (status) => {
    const reconciled = { ...action, status, acknowledged: true }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ task: { ...task, unknown_requires_ack: false, unknown_tool_actions: 0 }, events: [], actions: [reconciled], has_more: false })))
    const detail = await getTask('bee-1', task.id)
    expect(detail.actions[0]).toEqual(reconciled); expect(taskOutcomeUnknown(detail.task)).toBe(false)
    const html = await render(<ActionHistory actions={detail.actions} hasMore={false} />)
    expect(html).toContain('Resultado desconhecido reconhecido'); expect(html).not.toContain('ainda não reconhecido')
  })
})
describe('one recovery acknowledgment for independent journals', () => {
  it('distinguishes model, tool and mixed unknowns without inferring permission from history', () => {
    expect(taskUnknownKind(task)).toBe('tool')
    expect(taskUnknownKind({ ...task, unknown_model_calls: 1, unknown_tool_actions: 0 })).toBe('model')
    expect(taskUnknownKind({ ...task, unknown_model_calls: 1 })).toBe('mixed')
    expect(taskUnknownKind({ ...task, unknown_tool_actions: 0, unknown_requires_ack: false })).toBeNull()
  })
  it('invalidates acknowledgment after a new unknown action, aggregate change or task revision change', () => {
    const prior = taskAcknowledgmentKey(task, [action])
    expect(taskAcknowledgmentKey(task, [{ ...action }])).toBe(prior)
    expect(taskAcknowledgmentKey(task, [{ ...action, updated_at: '2026-10-06T18:01:00Z' }])).toBe(prior)
    expect(taskAcknowledgmentKey(task, [action, { ...action, id: 'action-2' }])).not.toBe(prior)
    expect(taskAcknowledgmentKey({ ...task, unknown_model_calls: 1 }, [action])).not.toBe(prior)
    expect(taskAcknowledgmentKey({ ...task, revision: 6 }, [action])).not.toBe(prior)
  })
  it('keeps pause and cancellation available as intents while a tool effect is in flight', () => {
    const dispatching = { ...task, action_in_flight: true }
    expect(taskControlBlocked(dispatching, 'resume')).toBe(true); expect(taskControlBlocked(dispatching, 'redirect')).toBe(true)
    expect(taskControlBlocked(dispatching, 'pause')).toBe(false); expect(taskControlBlocked(dispatching, 'cancel')).toBe(false)
  })
  it('uses the existing CAS resume command to acknowledge mixed unknowns once; never dispatches tools', async () => {
    const mixed = { ...task, unknown_model_calls: 1 }
    const resumed = { ...mixed, revision: 6, status: 'queued', unknown_requires_ack: false, unknown_model_calls: 0, unknown_tool_actions: 0 }
    const fetcher = vi.fn().mockResolvedValue(Response.json(resumed)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    const body = { action: 'resume' as const, expected_revision: mixed.revision, acknowledge_unknown: true }
    const identity = commandIdentity(() => 'request-1')
    await controlTask('bee-1', task.id, { ...body, client_request_id: identity.forPayload(body) })
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/tasks/task-1/control')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ ...body, client_request_id: 'request-1' })
  })
  it.each(['model', 'tool', 'mixed'] as const)('renders only one acknowledgment checkbox for %s outcomes', async (kind) => {
    const html = await render(<UnknownAcknowledgment kind={kind} checked={false} onChange={() => {}} />)
    expect(html.match(/type="checkbox"/g)).toHaveLength(1)
    if (kind === 'tool') expect(html).not.toContain('chamada ao modelo')
    if (kind === 'mixed') expect(html).toContain('modelo e das ferramentas')
  })
  it('shows action status, dates, obsolescence and recognition as read-only history', async () => {
    const html = await render(<ActionHistory actions={[action, { ...action, id: 'acknowledged-1', acknowledged: true, obsolete: true }, { ...action, id: 'ready-1', status: 'ready', acknowledged: false }]} hasMore={true} />)
    expect(html).toContain('text.normalize'); expect(html).toContain('Resultado desconhecido ainda não reconhecido'); expect(html).toContain('Resultado desconhecido reconhecido')
    expect(html).toContain('contexto anterior'); expect(html).toContain('Pronta para execução'); expect(html).toMatch(/datetime="2026-10-06T18:00:00Z"/i)
    expect(html).not.toContain('<button'); expect(html).not.toContain('<input')
  })
  it.each(['en', 'es'] as const)('translates mixed acknowledgment and action readiness in %s', async (language) => {
    const html = await render(<><UnknownAcknowledgment kind="mixed" checked={false} onChange={() => {}} /><ActionHistory actions={[{ ...action, status: 'ready' }]} hasMore={false} /></>, language)
    expect(html).toContain(language === 'en' ? 'Ready for execution' : 'Lista para ejecutar'); expect(html).not.toContain('mixedAcknowledgeUnknown')
  })
})
