import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { EnvironmentRecord } from '../../api/environments'
import type { ProvisioningPlan, ProvisioningReviewState } from '../../api/provisioning'
import { ErrorNotice } from '../../components/Feedback'
import { authorizationExpired, canAuthorizePlan, canPreparePlan, canRevokePlan } from './provisioningState'
import { useProvisioningReview } from './useProvisioningReview'

export function ProvisioningPlanSummary({ plan, state }: { plan: ProvisioningPlan; state: ProvisioningReviewState }) {
  const { t, i18n } = useTranslation('provisioning')
  const format = (bytes: number) => new Intl.NumberFormat(i18n.resolvedLanguage, { maximumFractionDigits: 2 }).format(bytes / 1024 ** 3)
  const fingerprint = state.host?.host_id === plan.plan.host_id ? state.host.fingerprint : '—'
  return <><h5>{t('intent')}</h5><dl className="approval-target"><div><dt>{t('host')}</dt><dd><code>{fingerprint}</code></dd></div><div><dt>{t('image')}</dt><dd>{t('imageDescription')}</dd></div></dl><p>{t('resources', { cpu: plan.plan.cpu_count, memory: format(plan.plan.memory_bytes), disk: format(plan.plan.disk_bytes) })}</p><p className="quiet-note">{t('safety')}</p>
    <details className="tool-contract"><summary>{t('technical')}</summary><dl className="approval-target"><div><dt>{t('planHash')}</dt><dd><code>{plan.plan_hash}</code></dd></div><div><dt>{t('isoHash')}</dt><dd><code>{plan.plan.image_iso_sha256}</code><p className="quiet-note">{t('fileSize', { size: format(plan.plan.image_iso_size) })}</p></dd></div><div><dt>{t('payloadHash')}</dt><dd><code>{plan.plan.payload_sha256}</code><p className="quiet-note">{t('fileSize', { size: format(plan.plan.payload_size) })}</p></dd></div></dl></details>
  </>
}
export function ProvisioningConfirmation({ action, plan, state, checked, busy, blocked, onChecked, onConfirm, onBack }: { action: 'authorize' | 'revoke'; plan: ProvisioningPlan; state: ProvisioningReviewState; checked: boolean; busy: boolean; blocked: boolean; onChecked: (checked: boolean) => void; onConfirm: () => void; onBack: () => void }) {
  const { t } = useTranslation('provisioning'); const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const authorizing = action === 'authorize'
  return <section className="tool-confirmation" aria-labelledby={`provisioning-confirm-${plan.plan_id}`}><h5 id={`provisioning-confirm-${plan.plan_id}`} tabIndex={-1} ref={heading}>{t(authorizing ? 'confirmAuthorize' : 'confirmRevoke')}</h5><p>{t(authorizing ? 'authorizeImpact' : 'revokeImpact', { code: state.host?.fingerprint ?? '—' })}</p>{authorizing && <label className="checkbox-row"><input type="checkbox" checked={checked} disabled={busy || blocked} onChange={(event) => onChecked(event.target.checked)} /><span>{t('authorizeChecked')}</span></label>}<div className="tool-actions"><button type="button" className="button secondary" disabled={busy} onClick={onBack}>{t('back')}</button><button type="button" className="button primary" disabled={busy || blocked || authorizing && !checked} onClick={onConfirm}>{t(busy ? 'authorizing' : authorizing ? 'confirmAuthorize' : 'confirmRevoke')}</button></div></section>
}
export function ProvisioningPlanValidity({ plan, now }: { plan: ProvisioningPlan; now: number }) {
  const { t, i18n } = useTranslation('provisioning')
  return <>{plan.status !== 'revoked' && plan.authorization_expires_at && <p className="quiet-note">{t('expires', { date: new Date(plan.authorization_expires_at).toLocaleString(i18n.resolvedLanguage) })}</p>}
    {plan.status === 'authorized' && authorizationExpired(plan, now) && <p className="form-error">{t('expired')}</p>}
    {!plan.context_valid && <p className="form-error">{t('stale')}</p>}
    {plan.status === 'outcome_unknown' && <p className="form-error">{t('unknown')}</p>}</>
}
export function ProvisioningReview({ agentId, environment, disabled = false }: { agentId: string; environment: EnvironmentRecord; disabled?: boolean }) {
  const { t, i18n } = useTranslation('provisioning'); const review = useProvisioningReview(agentId, environment)
  const { state, loading, busy, error, notice, selection, compared, now } = review
  const plan = state?.plan; const locked = disabled || loading || busy || error !== null
  const validAuthorization = plan && state ? canAuthorizePlan(plan, state, environment, now) : false
  const knownError = error && i18n.exists(`error_${error.code}`, { ns: 'provisioning' })
  return <section className="tool-access" aria-labelledby={`provisioning-${environment.id}`} aria-busy={loading || busy}>
    <div className="form-heading"><h5 id={`provisioning-${environment.id}`}>{t('title')}</h5><button type="button" className="text-button" disabled={busy || loading} onClick={review.refresh}>{t('refresh')}</button></div><p className="quiet-note">{t('description')}</p><p className="transfer-notice">{t('scope')}</p>
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}{notice && <p className={['uncertain', 'conflict', 'unknown'].includes(notice) ? 'form-error' : 'form-success'} role="status">{t(notice)}</p>}
    {error && <>{knownError ? <p className="form-error" role="alert">{t(`error_${error.code}`)}</p> : <ErrorNotice error={error} />}<p className="quiet-note">{t('readFailure')}</p></>}
    {state && <>{!state.host && <p className="quiet-note">{t('needsHost')}</p>}{state.host && !state.preparation_available && !plan && <p className="quiet-note">{t('unavailable')}</p>}
      {plan && <><p className="computer-status" role="status">{t(`status_${plan.status}`)}</p><ProvisioningPlanSummary plan={plan} state={state} /><ProvisioningPlanValidity plan={plan} now={now} />
        <div className="tool-actions">{validAuthorization && <button type="button" className="button primary" disabled={locked || selection !== null} onClick={() => review.choose('authorize')}>{t('authorize')}</button>}{canRevokePlan(plan) && <button type="button" className="button secondary" disabled={locked || selection !== null} onClick={() => review.choose('revoke')}>{t('revoke')}</button>}</div>
        {selection && <ProvisioningConfirmation key={selection.key + selection.action} action={selection.action} plan={plan} state={state} checked={compared} busy={busy} blocked={locked || selection.action === 'authorize' && !validAuthorization} onChecked={review.setCompared} onConfirm={() => { void review.mutate(selection.action) }} onBack={review.cancelSelection} />}
      </>}
      {canPreparePlan(state, environment) && <div className="tool-actions"><button type="button" className="button secondary" disabled={locked || selection !== null} onClick={() => { void review.mutate('prepare') }}>{t(busy ? 'preparing' : plan ? 'prepareAgain' : 'prepare')}</button></div>}
    </>}
  </section>
}
