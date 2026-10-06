import { useCallback, useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getApprovals } from '../../api/approvals'
import { ErrorNotice } from '../../components/Feedback'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { ApprovalCard } from './ApprovalCard'
import { mergeApprovalPage } from './state'

// Observation is read-only. Only a card's explicit confirmation sends a decision.
export function ApprovalPanel({ agentId, taskId, onChanged }: { agentId: string; taskId?: string; onChanged?: () => void }) {
  const { t } = useTranslation('approvals')
  const headingId = useId()
  const [pages, setPages] = useState(1)
  const [loadingMore, setLoadingMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now)
  const load = useCallback(async (signal: AbortSignal) => {
    try {
      let collection = await getApprovals(agentId, signal, 0, taskId)
      for (let page = 1; page < pages && collection.next_offset !== null; page++) {
        collection = mergeApprovalPage(collection, await getApprovals(agentId, signal, collection.next_offset, taskId))
      }
      return collection
    } finally { if (!signal.aborted) setLoadingMore(false) }
  }, [agentId, taskId, pages])
  const monitor = useTaskPolling(load)
  const refresh = monitor.refresh
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  useEffect(() => {
    window.addEventListener('bees:approvals-changed', refresh)
    return () => window.removeEventListener('bees:approvals-changed', refresh)
  }, [refresh])
  function changed() { window.dispatchEvent(new Event('bees:approvals-changed')); onChanged?.() }
  return <section className="approval-panel" aria-labelledby={headingId}>
    <div className="form-heading"><h3 id={headingId}>{t('title')}</h3><button type="button" className="text-button" disabled={busy || monitor.loading} onClick={monitor.refresh}>{t('refresh')}</button></div>
    <p className="quiet-note">{t('description')}</p>
    {(monitor.loading || loadingMore) && <p role="status" className="loading-notice">{t('loading')}</p>}
    {monitor.error && <><ErrorNotice error={monitor.error} /><p className="quiet-note">{t('monitorFailure')}</p></>}
    {monitor.data?.approvals.length === 0 && <p className="quiet-note">{t('empty')}</p>}
    <div className="approval-list">{monitor.data?.approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} now={now} disabled={busy || loadingMore || !!monitor.error} onBusy={setBusy} onChanged={changed} />)}</div>
    {monitor.data?.has_more && <button type="button" className="button secondary" disabled={busy || monitor.loading || loadingMore || !!monitor.error} onClick={() => { setLoadingMore(true); setPages((value) => value + 1) }}>{t('more')}</button>}
  </section>
}
