import { useTranslation } from 'react-i18next'
import type { ActionSummary } from '../../api/tasks'
import { TaskTime } from './TaskTime'

export function ActionHistory({ actions, hasMore }: { actions: ActionSummary[]; hasMore: boolean }) {
  const { t } = useTranslation('tasks')
  if (actions.length === 0) return null
  return <details className="task-events"><summary>{t('actionHistory')}</summary><p className="quiet-note">{t('actionHistoryScope')}</p>{hasMore && <p className="quiet-note">{t('actionLimited')}</p>}<ol>{actions.map((action) => <li key={action.id}>
    <strong>{action.tool_name}</strong><p>{t(`actionStatus.${action.status}`)}</p>
    <p className="quiet-note">{t('actionCreated')}: <TaskTime value={action.created_at} /><br />{t('actionUpdated')}: <TaskTime value={action.updated_at} /></p>
    {action.obsolete && <p className="quiet-note">{t('actionObsolete')}</p>}
    {(action.status === 'outcome_unknown' || action.acknowledged) && <p className="quiet-note">{t(action.acknowledged ? 'actionAcknowledged' : 'actionUnacknowledged')}</p>}
  </li>)}</ol></details>
}
