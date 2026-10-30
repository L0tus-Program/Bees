import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { getRecoveryInventory } from '../../api/recovery'
import type { RecoveryInventory } from '../../api/recovery'
import { actionStatuses } from '../../api/tasks'
import type { TaskRecord } from '../../api/tasks'
import './recovery.css'
import { RecoveryActions } from './RecoveryActions'

export function TaskRecovery({ task, onRefresh }: { task: TaskRecord; onRefresh: () => void }) {
  const { t, i18n } = useTranslation('recovery')
  const [inventory, setInventory] = useState<RecoveryInventory | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const operation = useRef<AbortController | null>(null), locked = useRef(false)
  useEffect(() => () => { operation.current?.abort() }, [])
  async function read() {
    if (locked.current) return
    locked.current = true; setBusy(true); setError(null); setInventory(null)
    const controller = new AbortController(); operation.current = controller
    try {
      const value = await getRecoveryInventory(task.agent_id, task.id, controller.signal)
      if (controller.signal.aborted) return
      if (value.task_revision !== task.revision) throw new ApiError('recovery_stale')
      setInventory(value)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure).code === 'recovery_stale' ? 'stale' : 'failure') }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  const format = (value: number) => new Intl.NumberFormat(i18n.resolvedLanguage).format(value)
  return <section className="task-recovery" aria-labelledby="task-recovery-heading" aria-busy={busy}>
    <h4 id="task-recovery-heading">{t('title')}</h4><p className="quiet-note">{t('scope')}</p>
    <button type="button" className="text-button" disabled={busy} onClick={() => { void read() }}>{t(busy ? 'loading' : inventory ? 'refresh' : 'read')}</button>
    {error && <p className="form-error" role="status">{t(error)}</p>}
    {error === 'stale' && <button type="button" className="text-button" onClick={onRefresh}>{t('refreshTask')}</button>}
    {inventory && <>
      <p className="quiet-note">{t('snapshot', { date: new Date(inventory.observed_at).toLocaleString(i18n.resolvedLanguage), revision: format(inventory.task_revision), runs: format(inventory.runs_total) })}</p>
      <div className="recovery-table"><table><caption className="sr-only">{t('title')}</caption><thead><tr><th scope="col">{t('state')}</th><th scope="col">{t('actions')}</th><th scope="col">{t('models')}</th></tr></thead><tbody>{[...actionStatuses, 'total' as const].map(status => <tr key={status}><th scope="row">{t(status)}</th><td>{format(inventory.actions[status])}</td><td>{format(inventory.model_calls[status])}</td></tr>)}</tbody></table></div>
      <div className="recovery-unknown">{(['actions', 'model_calls'] as const).map(group => <section key={group}><h5>{t(group === 'actions' ? 'actions' : 'models')}</h5><p>{t('unknown', { value: format(inventory[group].outcome_unknown) })}</p><p>{t('acknowledged', { value: format(inventory[group].unknown_acknowledged) })}</p><p>{t('unacknowledged', { value: format(inventory[group].unknown_unacknowledged) })}</p></section>)}</div>
      <p className="transfer-notice">{t('meaning')}</p>
    </>}
    <p className="quiet-note">{t('limits')}</p>
    <RecoveryActions task={task} onRefresh={onRefresh} />
  </section>
}
