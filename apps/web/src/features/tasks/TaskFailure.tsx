import { useTranslation } from 'react-i18next'
import type { TaskRecord } from '../../api/tasks'

/** Causa e evidência são distintas do estado incerto e dos controles de retomada. */
export function TaskFailure({ task, warnUnknown = false }: { task: TaskRecord; warnUnknown?: boolean }) {
  const { t, i18n } = useTranslation('workspace')
  const run = task.latest_run
  const code = run?.failure?.code ?? run?.error_code
  if (!code || !['paused', 'failed', 'cancelled'].includes(task.status)) return null
  const translated = i18n.exists(`errors.${code}`, { ns: 'product' })
  const message = translated ? i18n.t(`errors.${code}`, { ns: 'product' }) : t(code === 'outcome_unknown' ? 'failureUnknown' : 'failureOther')
  return <div className="my-1 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm" role="status">
    <p>{message}</p>
    {warnUnknown && task.unknown_requires_ack && <p className="mt-1 text-xs text-muted-foreground">{t(task.unknown_model_calls > 0 ? 'modelOutcomeUnknown' : 'toolOutcomeUnknown')}</p>}
    <details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">{t('failureDetails')}</summary>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 break-words">
        <dt>{t('failureCode')}</dt><dd>{code}</dd>
        {run?.model && <><dt>{t('failureModel')}</dt><dd>{run.model}</dd></>}
        {run?.provider && <><dt>{t('failureProvider')}</dt><dd>{run.provider}</dd></>}
        {run?.failure?.http_status && <><dt>HTTP</dt><dd>{run.failure.http_status}</dd></>}
        {run?.failure?.parameter && <><dt>{t('failureParameter')}</dt><dd>{run.failure.parameter}</dd></>}
      </dl>
    </details>
  </div>
}
