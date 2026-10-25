import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { getRecoveryActions } from '../../api/recovery'
import type { RecoveryActionPage } from '../../api/recovery'
import type { TaskRecord } from '../../api/tasks'
import { TaskTime } from './TaskTime'

type PageRequest = { after: string | null; history: (string | null)[] }
export function RecoveryActions({ task, onRefresh }: { task: TaskRecord; onRefresh: () => void }) {
  const { t, i18n } = useTranslation('recovery'), { t: tasks } = useTranslation('tasks')
  const [page, setPage] = useState<RecoveryActionPage | null>(null)
  const [current, setCurrent] = useState<PageRequest>({ after: null, history: [] })
  const [requested, setRequested] = useState<PageRequest | null>(null)
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false)
  const operation = useRef<AbortController | null>(null), locked = useRef(false)
  useEffect(() => () => { operation.current?.abort() }, [])
  async function read(target: PageRequest) {
    if (locked.current) return
    locked.current = true; setBusy(true); setPage(null); setError(null); setRequested(target)
    const controller = new AbortController(); operation.current = controller
    try {
      const result = await getRecoveryActions(task.agent_id, task.id, target.after, controller.signal)
      if (controller.signal.aborted) return
      if (result.task_revision !== task.revision) throw new ApiError('recovery_stale')
      setPage(result); setCurrent(target)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure).code === 'recovery_stale' ? 'stale' : 'historyError') }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  const format = (value: number) => new Intl.NumberFormat(i18n.resolvedLanguage).format(value)
  return <section className="recovery-actions" aria-labelledby="recovery-actions-heading" aria-busy={busy}>
    <h5 id="recovery-actions-heading">{t('records')}</h5><p className="quiet-note">{t('historyScope')}</p>
    <button type="button" className="text-button" disabled={busy} onClick={() => { void read({ after: null, history: [] }) }}>{t(busy ? 'historyLoading' : 'first')}</button>
    {error && <p className="form-error" role="status">{t(error)}</p>}
    {error === 'stale' ? <button type="button" className="text-button" onClick={onRefresh}>{t('refreshTask')}</button> : error && requested && <button type="button" className="text-button" onClick={() => { void read(requested) }}>{t('retryPage')}</button>}
    {page && <>
      <p className="quiet-note">{t('pageSnapshot', { page: format(current.history.length + 1), count: format(page.items.length), date: new Date(page.observed_at).toLocaleString(i18n.resolvedLanguage) })}</p>
      {page.items.length === 0 && <p role="status">{t('emptyPage')}</p>}
      <ol className="recovery-action-records">{page.items.map(item => <li key={item.id}>
        <h6>{item.tool_name === 'tool.intent' ? tasks('authorizationIntent') : item.tool_name}</h6><p>{tasks(`actionStatus.${item.status}`)}</p>
        <p className="quiet-note">{tasks(item.tool_name === 'tool.intent' && item.status === 'confirmed' ? 'authorizationUsedHelp' : `actionHelp.${item.status}`)}</p>
        {(item.status === 'outcome_unknown' || item.unknown_acknowledged) && <p className="quiet-note">{tasks(item.unknown_acknowledged ? 'actionAcknowledged' : 'actionUnacknowledged')}</p>}
        {item.executor_command && <p className="quiet-note">{tasks(`executorCommand.${item.executor_command}`)}</p>}
        {item.executor_code?.startsWith('late_') && <p className="transfer-notice">{tasks('executorLate', { status: tasks(`executorCommand.${item.executor_code.slice(5)}`, { defaultValue: item.executor_code.slice(5) }) })}</p>}
        <details><summary>{t('recordDetails')}</summary><dl><div><dt>{t('actionId')}</dt><dd><code>{item.id}</code></dd></div><div><dt>{t('runId')}</dt><dd><code>{item.run_id}</code></dd></div><div><dt>{t('recordRevision')}</dt><dd>{format(item.revision)}</dd></div></dl><p className="quiet-note">{tasks('actionCreated')}: <TaskTime value={item.created_at} /><br />{tasks('actionUpdated')}: <TaskTime value={item.updated_at} /></p></details>
      </li>)}</ol>
      <div className="task-actions"><button type="button" className="button secondary" disabled={busy || current.history.length === 0} onClick={() => { void read({ after: current.history.at(-1) ?? null, history: current.history.slice(0, -1) }) }}>{t('previous')}</button><button type="button" className="button secondary" disabled={busy || !page.has_more} onClick={() => { if (page.next_cursor) void read({ after: page.next_cursor, history: [...current.history, current.after] }) }}>{t('next')}</button></div>
    </>}
  </section>
}
