import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { decideApproval, revokeApproval } from '../../api/approvals'
import type { ApprovalDecision, ApprovalRecord } from '../../api/approvals'
import { ErrorNotice } from '../../components/Feedback'
import { commandIdentity } from '../tasks/state'
import { approvalDecidable, approvalRevocable } from './state'

const choices: ApprovalDecision[] = ['allow_once', 'allow_rule', 'ask', 'deny']
export function ApprovalCard({ approval, now, disabled, onChanged, onBusy }: { approval: ApprovalRecord; now: number; disabled: boolean; onChanged: () => void; onBusy: (busy: boolean) => void }) {
  const { t, i18n } = useTranslation('approvals')
  const [choice, setChoice] = useState<{ decision: ApprovalDecision; revision: number } | null>(null)
  const [revoking, setRevoking] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [identity] = useState(commandIdentity)
  const controller = useRef<AbortController | null>(null)
  const reasonId = useId()
  useEffect(() => () => controller.current?.abort(), [])
  const decidable = approvalDecidable(approval, now)
  const revocable = approvalRevocable(approval)
  const selected = choice?.revision === approval.revision && decidable ? choice.decision : null
  const confirmingRevoke = revoking === approval.revision && revocable
  const expires = new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(approval.expires_at))
  async function mutate(revoke = false) {
    if (busy || disabled || (revoke ? !confirmingRevoke : !selected)) return
    const operation = new AbortController(); controller.current = operation
    setBusy(true); onBusy(true); setError(null); setNotice(null)
    try {
      if (revoke) await revokeApproval(approval.agent_id, approval.id, approval.revision, operation.signal)
      else {
        const body = { expected_revision: approval.revision, decision: selected!, ...(reason.trim() ? { reason: reason.trim() } : {}) }
        await decideApproval(approval.agent_id, approval.id, { ...body, client_request_id: identity.forPayload(body) }, operation.signal)
      }
      if (!operation.signal.aborted) { identity.reset(); setChoice(null); setRevoking(null); setNotice(revoke ? 'revokedSaved' : 'decisionSaved'); onChanged() }
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); onChanged() } }
    finally { onBusy(false); if (!operation.signal.aborted) setBusy(false) }
  }
  return <article className={`approval-card approval-${approval.status}`} aria-busy={busy}>
    <div className="approval-heading"><h4>{approval.task_title}</h4><span className="approval-status">{t(approval.consumed_at ? 'consumed' : approval.status)}</span></div>
    {approval.decision && <p className="quiet-note">{t('lastDecision')}: {t(approval.decision)}{approval.decision === 'ask' && approval.status === 'pending' ? ` · ${t('stillWaiting')}` : ''}</p>}
    <dl className="approval-target">
      <div><dt>{t('action')}</dt><dd>{approval.tool_name === 'model' && approval.action === 'generate' ? t('generate') : `${approval.tool_name} · ${approval.action}`}</dd></div>
      <div><dt>{t('model')}</dt><dd>{approval.parameters.model}</dd></div>
      <div><dt>{t('destination')}</dt><dd><code>{approval.resource}</code></dd></div>
      <div><dt>{t('environment')}</dt><dd>{approval.environment_id === 'control_plane' ? t('control_plane') : approval.environment_id}</dd></div>
      <div><dt>{t('identity')}</dt><dd>{approval.identity === 'bees_user' ? t('bees_user') : approval.identity}</dd></div>
    </dl>
    {(approval.task_objective || approval.expected_result) && <details className="approval-objective"><summary>{t('reviewTask')}</summary>{approval.task_objective && <p><strong>{t('objective')}</strong><br />{approval.task_objective}</p>}{approval.expected_result && <p><strong>{t('expectedResult')}</strong><br />{approval.expected_result}</p>}</details>}
    <p className="quiet-note">{approval.tool_name === 'model' && approval.action === 'generate' ? t('cost') : approval.consequence || t('cost')}</p>
    <p className="quiet-note">{t('expiration', { time: expires })}</p>
    {!approval.consumed_at && ['pending', 'approved'].includes(approval.status) && (Date.parse(approval.expires_at) <= now ? <p className="transfer-notice">{t('expired')}</p> : !approval.valid && <p className="transfer-notice">{t('stale')}</p>)}
    {error && <><ErrorNotice error={error} /><p className="quiet-note">{t('uncertain')}</p></>}
    {notice && <p className="form-success" role="status">{t(notice)}</p>}
    {decidable && <div className="approval-choices" aria-label={t('reviewFirst')}>{choices.map((decision) => <button key={decision} type="button" className={`button secondary ${decision === 'deny' ? 'destructive-text' : ''}`} disabled={busy || disabled} aria-pressed={selected === decision} onClick={() => { setChoice({ decision, revision: approval.revision }); setNotice(null) }}>{t(decision)}</button>)}</div>}
    {selected && <form className="approval-confirmation" onSubmit={(event) => { event.preventDefault(); void mutate() }}><fieldset disabled={busy || disabled}>
      <h5>{t(selected)}</h5><p>{t(`help_${selected}`)}</p><p className="transfer-notice">{t(selected === 'allow_once' ? 'onceScope' : 'exactScope')}</p>
      <label className="field" htmlFor={reasonId}><span>{t('reason')}</span><textarea id={reasonId} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} rows={2} /></label>
      <div className="approval-actions"><button className="text-button" type="button" onClick={() => setChoice(null)}>{t('back')}</button><button className={`button ${selected === 'deny' ? 'danger' : 'primary'}`} type="submit">{t(busy ? 'saving' : 'confirm')}</button></div>
    </fieldset></form>}
    {revocable && <button type="button" className="text-button destructive-text" disabled={busy || disabled} onClick={() => setRevoking(approval.revision)}>{t('revoke')}</button>}
    {confirmingRevoke && <div className="approval-confirmation"><p className="transfer-notice">{t('revokeImpact')}</p><div className="approval-actions"><button type="button" className="text-button" disabled={busy || disabled} onClick={() => setRevoking(null)}>{t('back')}</button><button type="button" className="button danger" disabled={busy || disabled} onClick={() => { void mutate(true) }}>{t(busy ? 'saving' : 'confirmRevoke')}</button></div></div>}
  </article>
}
