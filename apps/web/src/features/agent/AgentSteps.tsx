import { useTranslation } from 'react-i18next'
import type { TaskRecord } from '../../api/tasks'

export function AgentSteps({ task }: { task: TaskRecord }) {
  const { t } = useTranslation('agent')
  const steps = task.agent_steps ?? []
  if (!steps.length) return null
  return <ol className="agent-steps" aria-label={t('stepsTitle')}>
    {steps.slice(-12).map((step, index) => <li key={`${step.at}:${index}`} className={`agent-step agent-step-${step.status ?? 'unknown'}`}>
      <span>{t(`functions.${step.tool}`, { defaultValue: step.tool })}</span>
      <small>{t(`stepStatus.${step.status ?? 'unknown'}`, { defaultValue: step.status ?? '' })}{step.code ? ` · ${step.code}` : ''}</small>
    </li>)}
  </ol>
}
