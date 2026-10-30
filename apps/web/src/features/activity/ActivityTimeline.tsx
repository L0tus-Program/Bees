import { useTranslation } from 'react-i18next'
import type { ActivityItem, ActivityPage } from '../../api/activity'
import { groupActivity } from './state'

export function ActivityTime({ value }: { value: string }) {
  const { i18n } = useTranslation('activity')
  return <time dateTime={value}>{new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(value))}</time>
}

function Fact({ item }: { item: ActivityItem }) {
  const { t, i18n } = useTranslation('activity')
  const tokens = (value: number) => t('tokens', { value: new Intl.NumberFormat(i18n.resolvedLanguage).format(value) })
  const statusKey = `status_${item.status}`
  const unknown = item.status === 'outcome_unknown' || item.usage?.status === 'unknown'
  const certainty = item.usage?.status === 'reserved' ? 'reserved' : item.usage?.status === 'released' ? 'released' : item.usage?.usage_kind ?? 'unknown'
  return <li className="activity-fact">
    <div className="activity-fact-heading"><strong>{t(`entity_${item.entity_type}`)}</strong><ActivityTime value={item.occurred_at} /></div>
    <p>{t(i18n.exists(statusKey, { ns: 'activity' }) ? statusKey : 'status_other')}</p>
    {unknown && <p className="transfer-notice">{t('unknownHelp')}</p>}
    {item.error_code && <p className="form-error">{t('error')}: {unknown ? <code>{item.error_code}</code> : i18n.exists(`errors.${item.error_code}`, { ns: 'product' }) ? i18n.t(`errors.${item.error_code}`, { ns: 'product' }) : item.error_code}</p>}
    {item.action_id && <p className="quiet-note">{t('action')}: <code>{item.action_id}</code></p>}
    <p className="quiet-note">{t('environment')}: {t(`environment_${item.environment.kind}`)}</p>
    {item.usage && <dl className="activity-fact-usage"><div><dt>{t('counted')}</dt><dd>{tokens(item.usage.counted_tokens)}</dd></div><div><dt>{t('certainty')}</dt><dd>{t(`certainty_${certainty}`)}</dd></div><div><dt>{t('source')}</dt><dd>{t(`source_${item.usage.source}`)}</dd></div></dl>}
    {item.usage && <details className="activity-details"><summary>{t('estimateSource')}</summary><p>{t(item.usage.estimate_method === 'chars_div_3_v1' ? 'chars_div_3_v1' : 'otherEstimate')}</p><code>{item.usage.estimate_method}</code><p>{t('reserved')}: {tokens(item.usage.reserved_tokens)}</p></details>}
  </li>
}

export function ActivityTimeline({ items, disabled, onOpen, onScope }: { items: ActivityItem[]; disabled: boolean; onOpen: (taskId: string) => void; onScope: (taskId: string, runId?: string) => void }) {
  const { t } = useTranslation('activity')
  return <div className="activity-timeline">{groupActivity(items).map((group) => <article className="activity-task" key={group.key}>
    <h3>{group.title ?? t('chat')}</h3>
    {group.taskId && <div className="activity-actions"><button className="button secondary" type="button" disabled={disabled} onClick={() => onOpen(group.taskId!)}>{t('openTask')}</button><button className="text-button" type="button" disabled={disabled} onClick={() => onScope(group.taskId!)}>{t('filterTask')}</button></div>}
    {group.runs.map((run) => <section className="activity-run" key={run.key}>
      <div className="activity-run-heading"><h4>{run.runId ? <>{t('run')}: <code>{run.runId}</code></> : t('noRun')}</h4>{run.runId && group.taskId && <button className="text-button" type="button" disabled={disabled} onClick={() => onScope(group.taskId!, run.runId!)}>{t('filterRun')}</button>}</div>
      {run.actions.map((action) => <section className="activity-action" key={action.key}>{action.actionId && <h5>{t('action')}: <code>{action.actionId}</code></h5>}<ol>{action.items.map((item) => <Fact key={item.id} item={item} />)}</ol></section>)}
    </section>)}
  </article>)}</div>
}

export function ActivitySummary({ page }: { page: ActivityPage }) {
  const { t, i18n } = useTranslation('activity')
  const number = new Intl.NumberFormat(i18n.resolvedLanguage)
  return <>
    <h3>{t('usage')}</h3><dl className="activity-stats">{(['counted', 'reported', 'estimated', 'reserved', 'unknown'] as const).map((key) => <div key={key}><dt>{t(key)}</dt><dd>{t('tokens', { value: number.format(page.usage[`${key}_tokens`]) })}</dd></div>)}<div><dt>{t('released')}</dt><dd>{t('counts', { value: number.format(page.usage.released_entries) })}</dd></div></dl>
    {page.usage.estimate_methods.length > 0 && <p className="quiet-note">{t('estimateSource')}: {page.usage.estimate_methods.map((method) => t(method === 'chars_div_3_v1' ? 'chars_div_3_v1' : 'otherEstimate')).join(' · ')}</p>}
    <p className="transfer-notice">{t('uncertainty')}</p>
    <h3>{t('operations')}</h3><dl className="activity-stats">{Object.entries(page.operations).map(([key, count]) => <div key={key}><dt>{t(key)}</dt><dd>{number.format(count)}</dd></div>)}</dl>
    <p className="quiet-note">{t('resolution')}</p>
  </>
}
