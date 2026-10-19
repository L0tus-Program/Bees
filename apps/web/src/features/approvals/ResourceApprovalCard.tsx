import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { ApprovalDecision } from '../../api/approvals'
import { getResourceApproval, resourceIntent, resourceSignature, sendResourceDecision } from '../../api/resourceTools'
import type { ResourceApproval, ResourceIntent } from '../../api/resourceTools'
import { ErrorNotice } from '../../components/Feedback'
import { TaskTime } from '../tasks/TaskTime'
import { clearResourceIntent, preserveResourceIntent, restoreResourceIntent } from './resourceRecovery'

const choices: ApprovalDecision[] = ['allow_once', 'allow_rule', 'ask', 'deny']
export function ResourceApprovalCard({ source, disabled, onChanged, onBusy }: { source: ResourceApproval; disabled: boolean; onChanged: () => void; onBusy: (value: boolean) => void }) {
  const { t } = useTranslation('resourceApprovals')
  const [recovery] = useState(() => restoreResourceIntent(source.agent_id, source.task_id, source.id))
  const [pending, setPending] = useState<ResourceIntent | null>(recovery.intent)
  const [corrupt, setCorrupt] = useState(recovery.corrupt)
  const [detail, setDetail] = useState<ResourceApproval | null>(null)
  const [selection, setSelection] = useState<{ decision: ApprovalDecision | 'revoke'; revision: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [storageError, setStorageError] = useState(false)
  const [reconciled, setReconciled] = useState(false)
  const [now, setNow] = useState(Date.now)
  const request = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const reviewHeading = useRef<HTMLHeadingElement>(null)
  const confirmationHeading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => { clearInterval(timer); request.current?.abort() } }, [])
  useEffect(() => { if (detail) reviewHeading.current?.focus() }, [detail])
  useEffect(() => { if (selection) confirmationHeading.current?.focus() }, [selection])
  const current = detail && detail.revision > source.revision ? detail : source
  const matching = detail !== null && resourceSignature(detail) === resourceSignature(current)
  const canDecide = matching && detail.revision === current.revision && detail.valid && current.valid && current.status === 'pending' && current.consumed_at === null && Date.parse(current.expires_at) > now
  const canRevoke = matching && ['pending', 'approved'].includes(current.status)
  const blocked = disabled || busy || pending !== null || corrupt
  const chosen = selection?.revision === current.revision && (selection.decision === 'revoke' ? canRevoke : canDecide) ? selection.decision : null
  const stale = !current.valid || matching && detail.revision === current.revision && !detail.valid
  async function review() {
    if (locked.current || disabled) return
    locked.current = true; setBusy(true); onBusy(true); setError(null)
    const controller = new AbortController(); request.current = controller
    try {
      const value = await getResourceApproval(source.agent_id, source.task_id, source.id, controller.signal)
      if (!controller.signal.aborted) { setDetail(value); setSelection(null); setReconciled(true); onChanged() }
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } onBusy(false) }
  }
  async function send(intent: ResourceIntent) {
    if (locked.current || disabled) return
    try { preserveResourceIntent(intent); setStorageError(false) } catch { setStorageError(true); return }
    locked.current = true; setBusy(true); onBusy(true); setPending(intent); setReconciled(false); setError(null); setNotice(null)
    const controller = new AbortController(); request.current = controller
    try {
      const value = await sendResourceDecision(intent, controller.signal)
      if (controller.signal.aborted) return
      clearResourceIntent(source.agent_id, source.task_id, source.id)
      setPending(null); setSelection(null); setDetail(value); setNotice('recorded'); onChanged()
    } catch (failure) { if (!controller.signal.aborted) { setError(asApiError(failure)); onChanged() } }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } onBusy(false) }
  }
  function confirm() {
    if (!chosen || blocked || !detail) return
    try { void send(resourceIntent(current, chosen)) } catch (failure) { setError(asApiError(failure)) }
  }
  function finishReview() {
    if (!detail || !matching || !reconciled || busy) return
    try {
      clearResourceIntent(source.agent_id, source.task_id, source.id)
      setPending(null); setCorrupt(false); setSelection(null); setNotice('reviewedOnly')
    } catch { setStorageError(true) }
  }
  return <article className="approval-card resource-approval" aria-busy={busy}>
    <div className="approval-heading"><h4>{t('transform')}</h4><strong>{t(current.consumed_at ? 'consumed' : current.status)}</strong></div>
    {current.effect_status === 'outcome_unknown' && <p className="transfer-notice" role="status">{t('unknown')}</p>}
    <p>{t('consequence')}</p>
    <dl className="approval-target"><div><dt>{t('operation')}</dt><dd>{t(current.operation)}</dd></div><div><dt>{t('target')}</dt><dd>{t('localResource')}</dd></div><div><dt>{t('identity')}</dt><dd>{t('beesUser')}</dd></div><div><dt>{t('size')}</dt><dd>{t('bytes', { count: current.input_bytes })}</dd></div></dl>
    <p className="quiet-note">{t('expires')} <TaskTime value={current.expires_at} /></p>
    {stale && !current.consumed_at && <p className="transfer-notice">{t(current.stale_reason === 'approval_expired' || Date.parse(current.expires_at) <= now ? 'expired' : 'stale')}</p>}
    {error && <ErrorNotice error={error} />}
    {notice && <p role="status">{t(notice)}</p>}
    {storageError && <p role="alert">{t('storageError')}</p>}
    {(pending || corrupt) && <div className="transfer-notice" role="status"><p>{t(corrupt ? 'corrupt' : 'uncertain')}</p><div className="approval-actions">
      {pending && <button type="button" className="button secondary" disabled={busy || disabled} onClick={() => { void send(pending) }}>{t('verify')}</button>}
      <button type="button" className="button secondary" disabled={busy || disabled} onClick={() => { void review() }}>{t('readCurrent')}</button>
      {detail && matching && reconciled && <button type="button" className="text-button" disabled={busy || disabled} onClick={finishReview}>{t('finishReview')}</button>}
    </div><small>{t('finishReviewHelp')}</small></div>}
    <button type="button" className="button secondary" disabled={busy || disabled} onClick={() => { void review() }}>{t('review')}</button>
    {matching && <section className="resource-review"><h5 tabIndex={-1} ref={reviewHeading}>{t('reviewHeading')}</h5><label className="field">{t('input')}<textarea readOnly value={detail.input_preview ?? ''} rows={4} /></label><p className="quiet-note">{t('redirect')}</p>
      <details><summary>{t('scope')}</summary><dl><dt>{t('resourceId')}</dt><dd>{current.target.resource_id}</dd><dt>{t('installation')}</dt><dd>{current.target.installation_id}</dd><dt>{t('toolVersion')}</dt><dd>{current.tool_version}</dd><dt>{t('pluginRevision')}</dt><dd>{current.scope.parameters.plugin_revision}</dd><dt>{t('grantRevision')}</dt><dd>{current.scope.parameters.grant_revision}</dd><dt>SHA-256</dt><dd>{current.input_hash}</dd></dl></details>
      <p className="quiet-note">{t('exact')}</p>
      {canDecide && <div className="approval-choices">{choices.map((choice) => <button key={choice} type="button" className={`button secondary ${choice === 'deny' ? 'destructive-text' : ''}`} aria-pressed={chosen === choice} disabled={blocked} onClick={() => { setSelection({ decision: choice, revision: current.revision }); setNotice(null) }}>{t(choice)}</button>)}</div>}
      {canRevoke && <button type="button" className="text-button destructive-text" disabled={blocked} onClick={() => setSelection({ decision: 'revoke', revision: current.revision })}>{t('revoke')}</button>}
    </section>}
    {chosen && <section className="approval-confirmation"><h5 ref={confirmationHeading} tabIndex={-1}>{t(chosen)}</h5><p>{t(`help_${chosen}`)}</p><div className="approval-actions"><button type="button" className="text-button" disabled={blocked} onClick={() => setSelection(null)}>{t('back')}</button><button type="button" className={`button ${chosen === 'deny' || chosen === 'revoke' ? 'danger' : 'primary'}`} disabled={blocked} onClick={confirm}>{t(busy ? 'saving' : 'confirm')}</button></div></section>}
  </article>
}
