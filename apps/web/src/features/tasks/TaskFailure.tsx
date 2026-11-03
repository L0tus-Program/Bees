import { useTranslation } from 'react-i18next'
import { failureMessage } from './failureMessage'
import type { TaskRecord } from '../../api/tasks'


/** Causa e evidência são distintas do estado incerto e dos controles de retomada. */
export function TaskFailure({ task, warnUnknown = false, compact = false }: { task: TaskRecord; warnUnknown?: boolean; compact?: boolean }) {
  const { t, i18n } = useTranslation('workspace')
  const run = task.latest_run
  const code = run?.failure?.code ?? run?.error_code
  if (!code || !['paused', 'failed', 'cancelled'].includes(task.status)) return null
  const message = failureMessage(i18n, code, run?.failure?.source, run?.failure?.response_issue)
  const step = task.agent_steps?.at(-1)
  const stepFailure = step?.code && step.code !== code && ['failed', 'rejected', 'outcome_unknown'].includes(step.status ?? '') ? `${step.tool}: ${failureMessage(i18n, step.code, 'tool')} · ${step.code}` : null
  if (compact) return <span className="task-failure-summary block line-clamp-2 text-xs text-foreground">{run?.failure?.tool_name && `${run.failure.tool_name}: `}{message}{stepFailure && ` · ${stepFailure}`}{run?.failure?.provider_message && ` · ${run.failure.provider_message}`} · {code}</span>
  return <div className="my-1 rounded-lg border border-warning/40 bg-warning/5 p-3 text-sm" role="status">
    <p>{message}</p>
    {stepFailure && <p className="mt-1 text-xs">{t('failureTool')}: {stepFailure}</p>}
    {run?.failure?.tool_name && <p className="mt-1 text-xs">{t('failureTool')}: {run.failure.tool_name}</p>}
    {run?.failure?.provider_message && <p className="mt-2 whitespace-pre-wrap break-words [overflow-wrap:anywhere]"><span className="font-medium">{t('failureProviderMessage')}: </span>{run.failure.provider_message}</p>}
    {warnUnknown && task.unknown_requires_ack && <p className="mt-1 text-xs text-muted-foreground">{t(task.unknown_model_calls > 0 ? 'modelOutcomeUnknown' : 'toolOutcomeUnknown')}</p>}
    <details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">{t('failureDetails')}</summary>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 break-words">
        <dt>{t('failureCode')}</dt><dd>{code}</dd>
        {run?.failure?.source !== 'tool' && run?.model && <><dt>{t('failureModel')}</dt><dd>{run.model}</dd></>}
        {run?.failure?.source !== 'tool' && run?.provider && <><dt>{t('failureProvider')}</dt><dd>{run.provider}</dd></>}
        {run?.failure?.http_status && <><dt>HTTP</dt><dd>{run.failure.http_status}</dd></>}
        {run?.failure?.parameter && <><dt>{t('failureParameter')}</dt><dd>{run.failure.parameter}</dd></>}
      </dl>
    </details>
  </div>
}
