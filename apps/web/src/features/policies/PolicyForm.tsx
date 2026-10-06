import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createPolicy, updatePolicy } from '../../api/policies'
import type { PolicyEffect, PolicyRecord } from '../../api/policies'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { editableGenerationPolicy, generationScope, policyRequestIdentity } from './state'

export function PolicyForm({ agent, policy, onSaved, onCancel, onReload }: { agent: AgentSummary; policy: PolicyRecord | null; onSaved: () => void; onCancel: () => void; onReload: () => void }) {
  const { t } = useTranslation('policies')
  const [name, setName] = useState(policy?.name ?? '')
  const [reason, setReason] = useState(policy?.reason ?? '')
  const [effect, setEffect] = useState<PolicyEffect>(policy?.effect ?? 'ask')
  const [scope, setScope] = useState<'all' | 'current' | 'saved'>(policy ? 'saved' : 'current')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  const identity = useRef(policyRequestIdentity())
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus(); return () => controller.current?.abort() }, [])
  const conflict = error?.status === 409
  const selectedScope = scope === 'saved' && policy && editableGenerationPolicy(policy, agent.id) ? policy.scope : generationScope(agent.provider_config, scope === 'current' && !!agent.provider_config)
  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy || conflict || (scope === 'current' && !agent.provider_config)) return
    const abort = new AbortController(); controller.current = abort; setBusy(true); setError(null)
    const body = { name: name.trim() || t('defaultName'), effect, scope: selectedScope, reason: reason.trim() }
    try {
      if (policy) await updatePolicy(agent.id, policy.id, { ...body, status: policy.status, expected_revision: policy.revision }, abort.signal)
      else await createPolicy(agent.id, { ...body, client_request_id: identity.current(body) }, abort.signal)
      if (!abort.signal.aborted) onSaved()
    } catch (failure) { if (!abort.signal.aborted) setError(asApiError(failure)) }
    finally { if (!abort.signal.aborted) setBusy(false) }
  }
  return <form className="policy-form" onSubmit={(event) => { void submit(event) }} aria-busy={busy}>
    <h3 ref={heading} tabIndex={-1}>{t(policy ? 'editRule' : 'newRule')}</h3>
    {policy?.origin === 'approval' && <p className="transfer-notice">{t('approvalEditImpact')}</p>}
    <fieldset disabled={busy}>
      <label className="field" htmlFor="policy-effect"><span>{t('effect')}</span><select id="policy-effect" value={effect} onChange={(event) => setEffect(event.target.value as PolicyEffect)}>{(['allow', 'ask', 'deny'] as const).map((choice) => <option key={choice} value={choice}>{t(choice)}</option>)}</select></label>
      {effect === 'ask' && <p className="quiet-note">{t('askHelp')}</p>}
      <label className="field" htmlFor="policy-scope"><span>{t('scope')}</span><select id="policy-scope" value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>{policy && <option value="saved">{t('savedScope')}</option>}<option value="current" disabled={!agent.provider_config}>{t('currentConnection')}</option><option value="all">{t('allGeneration')}</option></select></label>
      {!agent.provider_config && <p className="quiet-note">{t('noModel')}</p>}
      <div className="policy-target">{selectedScope.resource && <p><strong>{t('connection')}</strong><code>{selectedScope.resource}</code></p>}{typeof selectedScope.parameters?.model === 'string' && <p><strong>{t('model')}</strong><span>{selectedScope.parameters.model}</span></p>}{!selectedScope.resource && !selectedScope.parameters?.model && <p>{t('allGeneration')}</p>}</div>
      <label className="field" htmlFor="policy-name"><span>{t('name')} <small>{t('optional')}</small></span><input id="policy-name" maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="field" htmlFor="policy-reason"><span>{t('reason')} <small>{t('optional')}</small></span><textarea id="policy-reason" rows={2} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
    </fieldset>
    {error && (conflict ? <p role="alert" className="form-error">{t('conflict')}</p> : <ErrorNotice error={error} />)}
    <div className="policy-actions">{conflict && <button type="button" className="button secondary" onClick={onReload}>{t('reload')}</button>}<button type="button" className="button secondary" onClick={onCancel} disabled={busy}>{t('cancel')}</button><button type="submit" className="button primary" disabled={busy || conflict || (scope === 'current' && !agent.provider_config)}>{t(busy ? 'saving' : 'save')}</button></div>
  </form>
}
