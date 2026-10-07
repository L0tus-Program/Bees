import { useTranslation } from 'react-i18next'
import type { TaskRecord } from '../../api/tasks'
import { taskOutcomeUnknown, taskUnknownKind } from '../tasks/state'

export function DelegationCard({ task, selected, onSelect }: { task: TaskRecord; selected: boolean; onSelect: () => void }) {
  const { t } = useTranslation('product')
  const { t: tasks } = useTranslation('tasks')
  const unknown = taskOutcomeUnknown(task)
  const kind = taskUnknownKind(task)
  return <section className="delegation-card" aria-labelledby={`delegation-${task.id}`}>
    <p className="section-label">{t('chatDelegation')}</p>
    <h3 id={`delegation-${task.id}`}>{task.title}</h3>
    <p className="task-state" role="status">{tasks(`status.${task.status}`)}{task.control_requested && ` · ${tasks(`requested.${task.control_requested}`)}`}</p>
    {unknown && <p className="transfer-notice">{tasks(kind === 'tool' ? 'toolUnknownWarning' : kind === 'mixed' ? 'mixedUnknownWarning' : 'unknownWarning')}</p>}
    {task.action_in_flight && <p className="transfer-notice">{tasks('actionInFlightHelp')}</p>}
    {task.status === 'waiting_approval' && <p className="quiet-note">{t('chatDelegationApproval')}</p>}
    {task.status === 'failed' && !unknown && <p className="transfer-notice">{tasks('runFailed')}</p>}
    {!selected && task.latest_run?.result && <div className="task-result"><h4>{tasks('result')}</h4><p>{task.latest_run.result.content}</p><small>{tasks('reviewResult')}</small></div>}
    {task.status === 'completed' && !task.latest_run?.result && <p className="transfer-notice">{tasks('resultUnavailable')}</p>}
    <button type="button" className="button secondary" aria-pressed={selected} onClick={onSelect}>{t(selected ? 'chatDelegationClose' : 'chatDelegationManage')}</button>
  </section>
}
