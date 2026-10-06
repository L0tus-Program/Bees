import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { controlTask, getTask } from '../../api/tasks'
import type { TaskControl, TaskDetail as Detail, TaskRecord } from '../../api/tasks'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { commandIdentity, taskAcknowledgmentKey, taskControlBlocked, taskOutcomeUnknown, taskUnknownKind } from './state'
import { TaskTime } from './TaskTime'
import { useTaskPolling } from './useTaskPolling'
import { ApprovalPanel } from '../approvals/ApprovalPanel'
import { ActionHistory } from './ActionHistory'
import { UnknownAcknowledgment } from './UnknownAcknowledgment'

function mergeDetail(incoming: Detail, previous: Detail) { return previous.task.revision > incoming.task.revision ? previous : incoming }

export function TaskDetail({ agent, taskId, onRefresh, onClose }: { agent: AgentSummary; taskId: string; onRefresh: () => void; onClose: () => void }) {
  const agentId = agent.id
  const { t, i18n } = useTranslation('tasks')
  const load = useCallback((signal: AbortSignal) => getTask(agentId, taskId, signal), [agentId, taskId])
  const monitor = useTaskPolling(load, mergeDetail)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [intent, setIntent] = useState<TaskControl | null>(null)
  const [instruction, setInstruction] = useState('')
  const [acknowledgment, setAcknowledgment] = useState<{ snapshot: string; checked: boolean } | null>(null)
  const [confirmedTask, setConfirmedTask] = useState<TaskRecord | null>(null)
  const [identity] = useState(commandIdentity)
  const controller = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus(); return () => controller.current?.abort() }, [])
  const task = confirmedTask && (!monitor.data || confirmedTask.revision > monitor.data.task.revision) ? confirmedTask : monitor.data?.task
  const unknown = task ? taskOutcomeUnknown(task) : false
  const unknownKind = task ? taskUnknownKind(task) : null
  const acknowledgmentKey = task ? taskAcknowledgmentKey(task, monitor.data?.actions) : null
  const acknowledged = acknowledgment?.snapshot === acknowledgmentKey && acknowledgment?.checked === true
  const refresh = () => { monitor.refresh(); onRefresh() }

  async function command(action: TaskControl, snapshot: TaskRecord) {
    if (busy || !snapshot.available_controls.includes(action) || taskControlBlocked(snapshot, action) || (action === 'resume' && taskOutcomeUnknown(snapshot) && !acknowledged)) return
    setBusy(true); setError(null)
    const operation = new AbortController(); controller.current = operation
    const body = { expected_revision: snapshot.revision, action, ...(action === 'redirect' ? { instruction: instruction.trim() } : {}), ...(action === 'resume' && taskOutcomeUnknown(snapshot) ? { acknowledge_unknown: true } : {}) }
    try {
      const confirmed = await controlTask(agentId, taskId, { ...body, client_request_id: identity.forPayload(body) }, operation.signal)
      if (!operation.signal.aborted) { setConfirmedTask(confirmed); identity.reset(); setIntent(null); setInstruction(''); refresh() }
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); refresh() } }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  function confirm(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (task && intent) void command(intent, task) }
  function choose(action: TaskControl) {
    if (!task) return
    if (action === 'cancel' || action === 'redirect' || action === 'resume') { setIntent(action); setAcknowledgment(null) }
    else void command(action, task)
  }
  return <section className="task-detail" aria-labelledby="task-detail-heading">
    <div className="form-heading"><h3 id="task-detail-heading" tabIndex={-1} ref={heading}>{task?.title ?? t('details')}</h3><button type="button" className="text-button" onClick={onClose} disabled={busy}>{t('close')}</button></div>
    {monitor.loading && <LoadingNotice />}
    {monitor.error && <p className="form-error" role="status">{t('monitorFailure')}</p>}
    {error && <ErrorNotice error={error} />}
    {(monitor.error || error) && <button type="button" className="text-button" onClick={refresh}>{t('refresh')}</button>}
    {task && <>
      <p className="task-state" role="status">{t(`status.${task.status}`)}{task.control_requested && ` · ${t(`requested.${task.control_requested}`)}`}</p>
      <dl className="task-definition"><div><dt>{t('objective')}</dt><dd>{task.objective}</dd></div><div><dt>{t('expectedResult')}</dt><dd>{task.expected_result}</dd></div></dl>
      {task.progress && <p className="quiet-note">{i18n.exists(`progress.${task.progress.code}`, { ns: 'tasks' }) ? t(`progress.${task.progress.code}`) : t('eventFallback')} · <TaskTime value={task.progress.created_at} /></p>}
      {unknown && <p className="transfer-notice">{t(unknownKind === 'tool' ? 'toolUnknownWarning' : unknownKind === 'mixed' ? 'mixedUnknownWarning' : 'unknownWarning')}</p>}
      {task.action_in_flight && <p className="transfer-notice">{t('actionInFlightHelp')}</p>}
      {!unknown && task.latest_run?.error_code && <p className="transfer-notice">{i18n.exists(`errors.${task.latest_run.error_code}`, { ns: 'product' }) ? i18n.t(`errors.${task.latest_run.error_code}`, { ns: 'product' }) : t('runFailed')}</p>}
      {['policy_denied', 'policy_approval_required', 'invalid_policy'].includes(task.latest_run?.error_code ?? '') && <p className="quiet-note">{t('policyHelp')}</p>}
      <ApprovalPanel key={taskId} agentId={agentId} taskId={taskId} onChanged={refresh} />
      {task.latest_run?.result && <section className="task-result" aria-labelledby="task-result-heading"><h4 id="task-result-heading">{t('result')}</h4><p>{task.latest_run.result.content}</p><small><TaskTime value={task.latest_run.result.created_at} /> · {t('reviewResult')}</small></section>}
      {task.latest_run?.model && <p className="quiet-note">{t('usedModel')}: {task.latest_run.model}</p>}
      <div className="task-actions">{task.available_controls.map((action) => <button key={action} type="button" className={`button ${action === 'cancel' ? 'secondary destructive-text' : 'secondary'}`} disabled={busy || task.control_requested === action || taskControlBlocked(task, action)} onClick={() => choose(action)}>{t(`controls.${action}`)}</button>)}</div>
      {intent && task.available_controls.includes(intent) && <form className="task-command" onSubmit={confirm}>
        <fieldset disabled={busy}>
          {intent === 'cancel' && <p className="transfer-notice">{t('cancelImpact')}</p>}
          {intent === 'resume' && <p className="transfer-notice">{t('resumeTarget', { model: agent.provider_config?.model ?? '—' })}<br /><small>{agent.provider_config?.endpoint}</small></p>}
          {intent === 'redirect' && <label className="field" htmlFor="task-redirect"><span>{t('redirectInstruction')}</span><textarea id="task-redirect" rows={3} value={instruction} onChange={(event) => setInstruction(event.target.value)} required maxLength={12000} /><small>{t('redirectHelp')}</small></label>}
          {intent === 'resume' && unknown && <UnknownAcknowledgment kind={unknownKind!} checked={acknowledged} onChange={(checked) => setAcknowledgment({ snapshot: acknowledgmentKey!, checked })} />}
          <div className="task-actions"><button type="button" className="text-button" onClick={() => setIntent(null)}>{t('back')}</button><button type="submit" className={`button ${intent === 'cancel' ? 'danger' : 'primary'}`} disabled={taskControlBlocked(task, intent) || (intent === 'redirect' && !instruction.trim()) || (intent === 'resume' && unknown && !acknowledged)}>{t(busy ? 'sending' : 'confirm')}</button></div>
        </fieldset>
      </form>}
      {monitor.data && <ActionHistory actions={monitor.data.actions} hasMore={monitor.data.has_more} />}
      <details className="task-events"><summary>{t('activity')}</summary><ol>{monitor.data?.events.map((event) => <li key={event.id}><strong>{i18n.exists(`progress.${event.code}`, { ns: 'tasks' }) ? t(`progress.${event.code}`) : t('eventFallback')}</strong><TaskTime value={event.created_at} />{event.content && <p>{event.content}</p>}<small>{event.code}</small></li>)}</ol></details>
    </>}
  </section>
}
