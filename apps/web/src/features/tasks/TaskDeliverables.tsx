import { useTranslation } from 'react-i18next'
import type { DeliverableItem, TaskRecord } from '../../api/tasks'

function Item({ item }: { item: DeliverableItem }) {
  const { t, i18n } = useTranslation('tasks')
  const reason = item.error_code && (i18n.exists(`deliverables.errors.${item.error_code}`, { ns: 'tasks' }) ? t(`deliverables.errors.${item.error_code}`) : t('deliverables.errors.fallback'))
  return <li>
    <strong>{t(`deliverables.kind.${item.kind}`)} · {t(`deliverables.status.${item.status}`)}</strong>
    {reason && <small>{reason}</small>}
    {item.kind === 'table' && item.rows !== null && item.columns !== null && <small>{t('deliverables.tableShape', { rows: item.rows, columns: item.columns })}</small>}
    {(item.formula_like_cells ?? 0) > 0 && <small>{t('deliverables.formulas', { total: item.formula_like_cells })}</small>}
  </li>
}

/** Estado dos arquivos pedidos; o download continua nos cartões de artefatos verificados. */
export function TaskDeliverables({ task }: { task: TaskRecord }) {
  const { t } = useTranslation('tasks')
  const requested = task.deliverables ?? []
  if (!requested.length) return null
  const results = task.deliverable_results ?? null
  return <section className="task-deliverables" aria-labelledby="task-deliverables-heading">
    <h4 id="task-deliverables-heading">{t('deliverables.heading')}</h4>
    {results ? <ul>{results.items.map((item) => <Item key={item.kind} item={item} />)}</ul>
      : <p>{requested.map((kind) => t(`deliverables.kind.${kind}`)).join(' · ')} · {t(task.publication_pending ? 'deliverables.publicationPending' : 'deliverables.awaiting')}</p>}
    {results && task.publication_pending && <p>{t('deliverables.publicationPending')}</p>}
    {results?.partial && <div className="transfer-notice"><p>{t('deliverables.partial')}</p><ul>{results.missing_data.map((value, index) => <li key={index}>{value}</li>)}</ul></div>}
    <small>{t('deliverables.scope')}</small>
  </section>
}
