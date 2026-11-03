import { useTranslation } from 'react-i18next'
import type { ActionSummary } from '../../api/tasks'
import { TaskTime } from './TaskTime'
import { failureMessage } from './failureMessage'

export function ActionHistory({ actions, hasMore }: { actions: ActionSummary[]; hasMore: boolean }) {
  const { t, i18n } = useTranslation('tasks')
  if (actions.length === 0) return null
  return <details className="task-events"><summary>{t('actionHistory')}</summary><p className="quiet-note">{t('actionHistoryScope')}</p>{hasMore && <p className="quiet-note">{t('actionLimited')}</p>}<ol tabIndex={0} aria-label={t('actionHistory')}>{actions.map((action) => <li key={action.id}>
    <strong>{action.tool_name === 'tool.intent' ? t('authorizationIntent') : action.tool_name}</strong><p>{t(`actionStatus.${action.status}`)}</p>
    {action.error_code && <p className="whitespace-pre-wrap break-words">{failureMessage(i18n, action.error_code, 'tool')} · <code>{action.error_code}</code></p>}
    <p className="quiet-note">{t(action.tool_name === 'tool.intent' && action.status === 'confirmed' ? 'authorizationUsedHelp' : `actionHelp.${action.status}`)}</p>
    <p className="quiet-note">{t('actionCreated')}: <TaskTime value={action.created_at} /><br />{t('actionUpdated')}: <TaskTime value={action.updated_at} /></p>
    {action.obsolete && <p className="quiet-note">{t('actionObsolete')}</p>}
    {(action.status === 'outcome_unknown' || action.acknowledged) && <p className="quiet-note">{t(action.acknowledged ? 'actionAcknowledged' : 'actionUnacknowledged')}</p>}
  </li>)}</ol></details>
}
