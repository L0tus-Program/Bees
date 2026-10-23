import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentSummary } from '../../api/onboarding'
import { ApiError, asApiError } from '../../api/client'
import { definitionIntent, getRoutine, listRoutines, routineOccurrences, routineRevisions, routineRuntime, routineStatusIntent, sendRoutine } from '../../api/routines'
import type { Routine, RoutineDefinition, RoutineIntent, RoutineOccurrence, RoutineRevision, RoutineStatus } from '../../api/routines'
import { exportSkill, listSkills } from '../../api/skills'
import type { Skill, SkillDefinition } from '../../api/skills'
import { ErrorNotice } from '../../components/Feedback'
import { RoutineEditor } from './RoutineEditor'
import type { RoutineEditorState } from './RoutineEditor'
import { RoutineDescription, RoutineTime } from './RoutineDescription'
import { clearRoutineIntent, preserveRoutineIntent, restoreRoutineIntent } from './recovery'
import { TaskDetail } from '../tasks/TaskDetail'
import { ManualRoutineRun } from './ManualRoutineRun'
import { RoutineHook } from './RoutineHook'
import './routines.css'

type Review = { intent: RoutineIntent; definition: RoutineDefinition }
type History = { routine: Routine; occurrences: RoutineOccurrence[]; revisions: RoutineRevision[]; moreOccurrences: boolean; moreRevisions: boolean }
const reasons = new Set(['scheduled', 'misfire', 'overlap', 'skill_unavailable', 'routine_changed', 'task_created', 'task_completed', 'task_failed', 'task_cancelled', 'outcome_unknown', 'resource_unavailable', 'tool_unavailable', 'unsupported_capability', 'execution_not_enabled', 'runtime_changed', 'agent_inactive', 'skill_inputs_invalid'])

function fresh(skill: Skill, conversationId: string): RoutineDefinition {
  return { name: skill.current.definition.name, instructions: skill.current.definition.purpose, skill_id: skill.id, skill_version: skill.current_version,
    schedule: { frequency: 'daily', time: '09:00', weekdays: [] }, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC', inputs: {}, environment: skill.current.definition.resources.environment,
    destination: { kind: 'conversation', conversation_id: conversationId }, notification: 'always', grace_seconds: 60, misfire_policy: 'skip', overlap_policy: 'skip' }
}

export function RoutinesPanel({ agent }: { agent: AgentSummary }) {
  const { t } = useTranslation('routines')
  const [items, setItems] = useState<Routine[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [more, setMore] = useState(false)
  const [moreSkills, setMoreSkills] = useState(false)
  const [loading, setLoading] = useState(true)
  const [skillsLoading, setSkillsLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [skillError, setSkillError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editor, setEditor] = useState<RoutineEditorState | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [history, setHistory] = useState<History | null>(null)
  const [runtime, setRuntime] = useState(false)
  const [manualRuntime, setManualRuntime] = useState(false)
  const [manualRoutine, setManualRoutine] = useState<Routine | null>(null)
  const [manualBlocked, setManualBlocked] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [pending, setPending] = useState<RoutineIntent | null>(() => restoreRoutineIntent(agent.id))
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const reading = useRef<AbortController | null>(null)
  const writing = useRef<AbortController | null>(null)
  const operations = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const reviewHeading = useRef<HTMLHeadingElement | null>(null)
  const historyHeading = useRef<HTMLHeadingElement | null>(null)
  const noticeFocus = useRef<HTMLParagraphElement | null>(null)
  const returnFocus = useRef<HTMLButtonElement | null>(null)
  useEffect(() => { reviewHeading.current?.focus() }, [review])
  useEffect(() => { historyHeading.current?.focus() }, [history])
  useEffect(() => { if (notice) noticeFocus.current?.focus() }, [notice])
  const load = useCallback(async (offset = 0) => {
    reading.current?.abort()
    const controller = new AbortController(); reading.current = controller
    setLoading(true)
    try {
      const [page, runtime] = await Promise.all([listRoutines(agent.id, offset, controller.signal), routineRuntime(agent.id, controller.signal).catch(() => ({ available: false, manual_available: false }))])
      if (controller.signal.aborted) return
      setItems((old) => offset ? [...old, ...page.filter((item) => !old.some((previous) => previous.id === item.id))] : page)
      setMore(page.length === 50); setError(null)
      setRuntime(runtime.available)
      setManualRuntime(runtime.manual_available)
      return true
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)); return false }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [agent.id])
  const loadSkills = useCallback(async (offset = 0, signal?: AbortSignal) => {
    setSkillsLoading(true)
    try {
      const page = await listSkills(agent.id, offset, signal)
      if (signal?.aborted) return
      setSkills((old) => offset ? [...old, ...page.filter((item) => !old.some((previous) => previous.id === item.id))] : page)
      setMoreSkills(page.length === 50); setSkillError(null)
    } catch (failure) { if (!signal?.aborted) setSkillError(asApiError(failure)) }
    finally { if (!signal?.aborted) setSkillsLoading(false) }
  }, [agent.id])
  useEffect(() => {
    const controller = new AbortController()
    void load(); void loadSkills(0, controller.signal)
    return () => { controller.abort(); reading.current?.abort(); writing.current?.abort(); operations.current?.abort() }
  }, [load, loadSkills])

  async function send(intent: RoutineIntent) {
    if (locked.current) return
    locked.current = true
    reading.current?.abort(); operations.current?.abort()
    const controller = new AbortController(); writing.current = controller
    setBusy(true); setLoading(false); setPending(intent); setError(null); setNotice(null)
    setStorageUnavailable(!preserveRoutineIntent(intent))
    try {
      await sendRoutine(intent, controller.signal)
      if (controller.signal.aborted) return
      clearRoutineIntent(agent.id); setPending(null); setEditor(null); setReview(null); setHistory(null)
      setStorageUnavailable(false); setNotice('saved'); await load()
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asApiError(failure); setError(problem)
      if (problem.status >= 400 && problem.status < 500 && problem.code !== 'invalid_response') {
        clearRoutineIntent(agent.id); setPending(null); setStorageUnavailable(false)
        if (problem.status === 409) { setEditor(null); setReview(null); setHistory(null); setNotice('conflict'); await load() }
      }
    } finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  async function verify() {
    if (!pending || locked.current) return
    const intent = pending
    locked.current = true; setBusy(true); setError(null)
    const controller = new AbortController(); operations.current = controller
    try {
      if (intent.routineId) await getRoutine(agent.id, intent.routineId, controller.signal)
      else await listRoutines(agent.id, 0, controller.signal)
      if (controller.signal.aborted) return
      locked.current = false; setBusy(false)
      await send(intent)
    } catch (failure) { if (!controller.signal.aborted) { setError(asApiError(failure)); locked.current = false; setBusy(false) } }
  }
  async function openEditor(routine?: Routine) {
    if (locked.current || pending) return
    const definition = routine?.definition
    if (!definition && (!skills[0] || !agent.conversation_id)) return
    locked.current = true; setBusy(true); setError(null)
    const controller = new AbortController(); operations.current?.abort(); operations.current = controller
    try {
      const value = definition ?? { ...fresh(skills[0], agent.conversation_id!), ...(routine ? { name: routine.name, instructions: routine.instructions } : {}) }
      const current = skills.find((item) => item.id === value.skill_id && item.current_version === value.skill_version)
      const source = current?.current.definition ?? (await exportSkill(agent.id, value.skill_id, value.skill_version)).definition
      if (controller.signal.aborted) return
      setEditor({ definition: value, source, previous: routine }); setReview(null); setHistory(null)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  function prepare(definition: RoutineDefinition, previous: Routine | undefined, source: SkillDefinition) {
    try { setEditor((old) => old ? { ...old, definition, source } : old); setReview({ intent: definitionIntent(agent.id, definition, previous), definition }); setError(null) }
    catch (failure) { setError(asApiError(failure)) }
  }
  function prepareStatus(routine: Routine, status: RoutineStatus) {
    if (!routine.definition) return
    try { setReview({ intent: routineStatusIntent(routine, status), definition: routine.definition }); setEditor(null); setError(null); setHistory(null) }
    catch (failure) { setError(asApiError(failure)) }
  }
  async function showHistory(routine: Routine, section?: 'occurrences' | 'revisions') {
    if (locked.current) return
    locked.current = true; setBusy(true); setError(null)
    const controller = new AbortController(); operations.current?.abort(); operations.current = controller
    try {
      const [occurrences, revisions] = await Promise.all([
        section === 'revisions' ? Promise.resolve<null>(null) : routineOccurrences(agent.id, routine.id, section === 'occurrences' ? (history?.occurrences.length ?? 0) : 0, controller.signal),
        section === 'occurrences' ? Promise.resolve<null>(null) : routineRevisions(agent.id, routine.id, section === 'revisions' ? (history?.revisions.length ?? 0) : 0, controller.signal),
      ])
      if (controller.signal.aborted) return
      setHistory((old) => ({ routine,
        occurrences: occurrences ? section === 'occurrences' && old?.routine.id === routine.id ? [...old.occurrences, ...occurrences.filter((item) => !old.occurrences.some((previous) => previous.id === item.id))] : occurrences : old?.occurrences ?? [],
        revisions: revisions ? section === 'revisions' && old?.routine.id === routine.id ? [...old.revisions, ...revisions.filter((item) => !old.revisions.some((previous) => previous.revision === item.revision))] : revisions : old?.revisions ?? [],
        moreOccurrences: occurrences ? occurrences.length === 50 : old?.moreOccurrences ?? false,
        moreRevisions: revisions ? revisions.length === 50 : old?.moreRevisions ?? false,
      }))
      setEditor(null); setReview(null)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  const disabled = loading || busy || pending !== null || manualBlocked || manualRoutine !== null
  const reviewEffect = review?.intent.action === 'status' ? review.intent.body.status === 'active' ? review.definition.execution_mode === 'scheduled' ? 'scheduledEffect' : 'eligibleEffect' : review.intent.body.status === 'paused' ? 'pauseEffect' : 'cancelEffect' : 'saveEffect'
  return <section className="surface routines-panel" aria-labelledby="routines-heading" aria-busy={loading || busy}>
    <div className="form-heading"><h2 id="routines-heading">{t('title')}</h2><button type="button" className="text-button" disabled={busy || loading} onClick={() => { void load(); void loadSkills() }}>{t('refresh')}</button></div>
    <p>{t('description')}</p><p className="routine-unavailable" role="status">{t(runtime ? 'runtimeReady' : 'unavailable')}</p>
    {(loading || busy) && <p role="status">{t('loading')}</p>}
    {error && <ErrorNotice error={error} />}{skillError && <ErrorNotice error={skillError} />}{notice && <p role="status" tabIndex={-1} ref={noticeFocus}>{t(notice)}</p>}
    {pending && !busy && <div className="transfer-notice" role="status"><p>{t('lost')}</p><button type="button" className="button secondary" onClick={() => { void verify() }}>{t('verify')}</button></div>}
    {storageUnavailable && <p role="status">{t('storageUnavailable')}</p>}
    {!skillsLoading && skills.length === 0 && <p>{t('noSkills')}</p>}
    {!agent.conversation_id && <p>{t('noConversation')}</p>}
    <ManualRoutineRun key={`manual:${agent.id}`} agentId={agent.id} routine={manualRoutine} available={manualRuntime} onLock={setManualBlocked} onClose={() => { setManualRoutine(null); returnFocus.current?.focus(); void load() }} onCreated={(id) => { setManualRoutine(null); setTaskId(id); void load() }} />
    <div className="routine-actions"><button type="button" className="button secondary" disabled={disabled || skillsLoading || skills.length === 0 || !agent.conversation_id || !!error || !!skillError} onClick={(event) => { returnFocus.current = event.currentTarget; void openEditor() }}>{t('create')}</button>{moreSkills && <button type="button" className="text-button" disabled={disabled || skillsLoading} onClick={() => { void loadSkills(skills.length) }}>{t('moreSkills')}</button>}</div>
    {editor && !review && <RoutineEditor key={`${editor.previous?.id ?? 'new'}:${editor.previous?.revision ?? 0}`} initial={editor} skills={skills} conversationId={agent.conversation_id} disabled={disabled} onReview={prepare} onCancel={() => { setEditor(null); returnFocus.current?.focus() }} />}
    {review && <section className="routine-review" aria-labelledby="routine-review-heading"><h3 id="routine-review-heading" tabIndex={-1} ref={reviewHeading}>{t('reviewTitle')}</h3><RoutineDescription definition={review.definition} /><p>{t(reviewEffect)}</p>{!runtime && <p>{t('unavailable')}</p>}<div className="routine-actions"><button type="button" className="button primary" disabled={disabled} onClick={() => { void send(review.intent) }}>{t('confirm')}</button><button type="button" className="button secondary" disabled={disabled} onClick={() => { setReview(null); if (!editor) returnFocus.current?.focus() }}>{t('back')}</button></div></section>}
    {!loading && !error && items.length === 0 && <p>{t('empty')}</p>}
    <ul className="routine-list">{items.map((routine) => <li key={routine.id}>
      <h3>{routine.name}</h3><p>{t('revision', { revision: routine.revision })} · {t(routine.status)}</p>
      {routine.origin === 'legacy' && <p>{t('legacy')}</p>}
      {routine.definition && <p>{t(routine.definition.execution_mode === 'agent' ? 'agentExecution' : routine.definition.execution_mode === 'scheduled' ? 'scheduledExecution' : 'prepareOnly')}</p>}
      <p>{t('next')}: {routine.next_run_at ? <RoutineTime value={routine.next_run_at} timezone={routine.timezone} /> : t('noNext')}</p>
      {routine.definition && <p>{t(routine.definition.schedule.frequency)} · {routine.definition.schedule.time} ({routine.timezone}) · {t(`notification_${routine.definition.notification}`)}</p>}
      <div className="routine-actions">
        {routine.definition && routine.status !== 'cancelled' && <button type="button" className="button secondary" disabled={disabled || !manualRuntime || !!error} onClick={(event) => { returnFocus.current = event.currentTarget; setManualRoutine(routine); setEditor(null); setReview(null); setHistory(null); setTaskId(null) }}>{t('manual')}</button>}
        {routine.status !== 'cancelled' && <button type="button" className="button secondary" disabled={disabled || skillsLoading || !!error || routine.origin === 'legacy' && (skills.length === 0 || !agent.conversation_id)} onClick={(event) => { returnFocus.current = event.currentTarget; void openEditor(routine) }}>{t('edit')}</button>}
        {routine.definition && routine.status !== 'cancelled' && <><button type="button" className="button secondary" disabled={disabled || !!error} onClick={(event) => { returnFocus.current = event.currentTarget; prepareStatus(routine, routine.status === 'active' ? 'paused' : 'active') }}>{t(routine.status === 'active' ? 'pause' : 'eligible')}</button><button type="button" className="text-button" disabled={disabled || !!error} onClick={(event) => { returnFocus.current = event.currentTarget; prepareStatus(routine, 'cancelled') }}>{t('cancelRoutine')}</button></>}
        <button type="button" className="text-button" disabled={disabled} onClick={() => { void showHistory(routine) }}>{t('history')}</button>
      </div>
      {routine.definition && routine.status !== 'cancelled' && <RoutineHook agentId={agent.id} routineId={routine.id} />}
    </li>)}</ul>
    {more && <button type="button" className="button secondary" disabled={disabled} onClick={() => { void load(items.length) }}>{t('more')}</button>}
    {history && <section className="routine-history" aria-labelledby="routine-history-heading"><h3 id="routine-history-heading" tabIndex={-1} ref={historyHeading}>{history.routine.name} · {t('history')}</h3>
      {history.occurrences.length === 0 && <p>{t('historyEmpty')}</p>}
      <ol>{history.occurrences.map((occurrence) => <li key={occurrence.id}><p><strong>{t(`occurrence_${occurrence.status}`)}</strong> · {t('revision', { revision: occurrence.routine_revision })}{occurrence.metadata.origin === 'manual' && <> · {t('manualOrigin')}</>}</p><RoutineTime value={occurrence.scheduled_at} timezone={history.revisions.find((item) => item.revision === occurrence.routine_revision)?.definition.timezone ?? 'UTC'} /><p>{t(`reason_${reasons.has(occurrence.reason) ? occurrence.reason : 'other'}`)}</p>{typeof occurrence.metadata.task_status === 'string' && ['queued', 'running', 'waiting_approval', 'waiting_resource', 'paused', 'completed', 'failed', 'cancelled'].includes(occurrence.metadata.task_status) && <p>{t('linkedTask')}: {t(`status.${occurrence.metadata.task_status}`, { ns: 'tasks' })}</p>}{occurrence.task_id && <button type="button" className="text-button" onClick={() => setTaskId(occurrence.task_id)}>{t('openTask')}</button>}{occurrence.window_end !== occurrence.scheduled_at && <p>{t('range')}: <RoutineTime value={occurrence.window_end} timezone={history.revisions.find((item) => item.revision === occurrence.routine_revision)?.definition.timezone ?? 'UTC'} /></p>}</li>)}</ol>
      {history.moreOccurrences && <button type="button" className="button secondary" disabled={disabled} onClick={() => { void showHistory(history.routine, 'occurrences') }}>{t('moreHistory')}</button>}
      <h4>{t('revisions')}</h4>{history.revisions.map((revision) => <details key={revision.revision}><summary>{t('revision', { revision: revision.revision })} · <RoutineTime value={revision.created_at} timezone={revision.definition.timezone} /></summary><RoutineDescription definition={revision.definition} /></details>)}
      {history.moreRevisions && <button type="button" className="button secondary" disabled={disabled} onClick={() => { void showHistory(history.routine, 'revisions') }}>{t('moreRevisions')}</button>}
    </section>}
    {taskId && <TaskDetail key={`routine-task:${taskId}`} agent={agent} taskId={taskId} onClose={() => setTaskId(null)} onRefresh={() => { if (history) void showHistory(history.routine) }} />}
  </section>
}
