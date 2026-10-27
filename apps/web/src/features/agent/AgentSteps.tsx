import { useTranslation } from 'react-i18next'
import type { TaskRecord } from '../../api/tasks'

export function AgentSteps({ task }: { task: TaskRecord }) {
  const { t, i18n } = useTranslation('agent')
  const steps = task.agent_steps ?? []
  if (!steps.length) return null
  return <ol className="agent-steps" aria-label={t('stepsTitle')}>
    {steps.slice(-12).map((step, index) => <li key={`${step.at}:${index}`} className={`agent-step agent-step-${step.status ?? 'unknown'}`}>
      <span>{t(`functions.${step.tool}`, { defaultValue: step.tool.replace(/_/g, ' ') })}</span>
      <small>{t(`stepStatus.${step.status ?? 'unknown'}`, { defaultValue: step.status ?? '' })}{step.code ? ` · ${i18n.exists(`stepCode.${step.code}`, { ns: 'agent' }) ? t(`stepCode.${step.code}`) : t('stepCodeOther', { code: step.code })}` : ''}</small>
    </li>)}
  </ol>
}

export function ResearchSummary({ task }: { task: TaskRecord }) {
  const { t } = useTranslation('agent')
  const research = task.research
  if (!research || research.pending) return research?.pending ? <p className="quiet-note">{t('researchPending')}</p> : null
  return <div className="research-summary">
    <p>{t('researchCheck', { read: research.sources_read ?? 0, cited: research.links_cited ?? 0, verified: research.links_verified ?? 0 })}</p>
    {research.no_citations && <p className="transfer-notice">{t('researchNoCitations')}</p>}
    {!!research.unverified_links?.length && <details><summary>{t('researchUnverified', { count: research.unverified_links.length })}</summary><ul>{research.unverified_links.map((url) => <li key={url}><code>{url}</code></li>)}</ul></details>}
    {!!research.artifacts?.length && <ul className="research-artifacts">{research.artifacts.map((item) => <li key={item.artifact_id}><a href={`/api/v1/agents/${encodeURIComponent(task.agent_id)}/artifacts/${encodeURIComponent(item.artifact_id)}/download`}>{item.name}</a></li>)}</ul>}
  </div>
}
