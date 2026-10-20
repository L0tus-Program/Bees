import { useCallback, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getResourceApproval, getResourceApprovals } from '../../api/resourceTools'
import { ErrorNotice } from '../../components/Feedback'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { ResourceApprovalCard } from './ResourceApprovalCard'
import { pendingResourceIds } from './resourceRecovery'
import './resourceApprovals.css'

export default function ResourceApprovalPanel({ agentId, taskId, onChanged }: { agentId: string; taskId: string; onChanged: () => void }) {
  const { t } = useTranslation('resourceApprovals')
  const headingId = useId()
  const [pages, setPages] = useState(1)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async (signal: AbortSignal) => {
    const first = await getResourceApprovals(agentId, taskId, 0, signal)
    const items = new Map(first.approvals.map((item) => [item.id, item]))
    let more = first.has_more
    for (let page = 1; page < pages && more; page++) {
      const next = await getResourceApprovals(agentId, taskId, page * 50, signal)
      next.approvals.forEach((item) => items.set(item.id, item)); more = next.has_more
    }
    for (const id of pendingResourceIds(agentId, taskId)) {
      if (!items.has(id)) {
        const value = await getResourceApproval(agentId, taskId, id, signal)
        items.set(id, { ...value, input_preview: null })
      }
    }
    return { approvals: [...items.values()], has_more: more }
  }, [agentId, taskId, pages])
  const monitor = useTaskPolling(load)
  function changed() { monitor.refresh(); onChanged() }
  return <section className="resource-approval-panel" aria-labelledby={headingId}>
    <div className="form-heading"><h3 id={headingId}>{t('title')}</h3><button type="button" className="text-button" disabled={busy || monitor.loading} onClick={monitor.refresh}>{t('refresh')}</button></div>
    <p className="quiet-note">{t('description')}</p>
    {monitor.loading && <p role="status">{t('loading')}</p>}
    {monitor.error && <ErrorNotice error={monitor.error} />}
    {monitor.data?.approvals.length === 0 && <p>{t('empty')}</p>}
    {monitor.data?.approvals.map((item) => <ResourceApprovalCard key={item.id} source={item} disabled={busy || !!monitor.error} onBusy={setBusy} onChanged={changed} />)}
    {monitor.data?.has_more && <button type="button" className="button secondary" disabled={busy || monitor.loading || !!monitor.error} onClick={() => setPages((value) => value + 1)}>{t('more')}</button>}
  </section>
}
