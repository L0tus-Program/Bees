import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { cancelEnvironment, createEnvironment } from '../../api/environments'
import type { EnvironmentPlan, EnvironmentRecord } from '../../api/environments'
import { ErrorNotice } from '../../components/Feedback'
import { EnvironmentForm } from './EnvironmentForm'
import { loadEnvironments, planIdentity, uncertainEnvironmentMutation } from './state'
import type { EnvironmentWorkspace } from './state'

export function EnvironmentPanel({ agent }: { agent: AgentSummary }) {
  const { t, i18n } = useTranslation('environments')
  const [data, setData] = useState<EnvironmentWorkspace | null>(null)
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null); const [notice, setNotice] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0); const [cancelling, setCancelling] = useState<EnvironmentRecord | null>(null)
  const identify = useRef(planIdentity()); const cancellationId = useRef('')
  const read = useRef<AbortController | null>(null); const mutation = useRef<AbortController | null>(null)
  const reconcileNotice = useRef<string | null>(null)
  useEffect(() => {
    const controller = new AbortController(); read.current = controller
    void loadEnvironments(agent.id, controller.signal).then((workspace) => {
      if (!controller.signal.aborted) { setData(workspace); setLoading(false); setError(null); if (reconcileNotice.current) { setNotice(reconcileNotice.current); reconcileNotice.current = null } }
    }, (failure: unknown) => { if (!controller.signal.aborted) { setError(asApiError(failure)); setLoading(false) } })
    return () => controller.abort()
  }, [agent.id, attempt])
  useEffect(() => () => mutation.current?.abort(), [])
  function refresh() { read.current?.abort(); setLoading(true); setCancelling(null); setError(null); setAttempt((value) => value + 1) }
  async function mutate(operation: (signal: AbortSignal) => Promise<EnvironmentRecord>, success: string) {
    if (busy || loading || error) return
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setNotice(null)
    try {
      const result = await operation(controller.signal)
      if (!controller.signal.aborted) {
        if (success === 'saved') identify.current = planIdentity()
        setNotice(success === 'saved' && result.status === 'cancelled' ? 'replayedCancelled' : result.status === 'outcome_unknown' ? 'unknown' : success); refresh(); window.dispatchEvent(new Event('bees:environments-changed'))
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const issue = asApiError(failure)
        if (uncertainEnvironmentMutation(issue) || issue.status === 409) {
          const message = uncertainEnvironmentMutation(issue) ? 'uncertain' : 'conflict'
          setNotice(message); reconcileNotice.current = message === 'uncertain' ? 'reconciled' : 'conflict'; refresh()
          window.dispatchEvent(new Event('bees:environments-changed'))
        } else setError(issue)
      }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  function save(plan: EnvironmentPlan) { const id = identify.current(plan); void mutate((signal) => createEnvironment(agent.id, plan, id, signal), 'saved') }
  function cancel() { if (cancelling) { const current = cancelling; const id = cancellationId.current; void mutate((signal) => cancelEnvironment(agent.id, current, id, signal), 'cancelled') } }
  const locked = loading || busy || error !== null || cancelling !== null
  const knownError = error && i18n.exists(`error_${error.code}`, { ns: 'environments' })
  return <section className="surface computer-panel" aria-labelledby="computer-heading" aria-busy={loading || busy}>
    <div className="form-heading"><h2 id="computer-heading">{t('title', { name: agent.name })}</h2><button className="button secondary" type="button" onClick={refresh} disabled={busy || loading}>{t('refresh')}</button></div>
    <p className="form-introduction">{t('description')}</p><p className="transfer-notice">{t('scope')}</p>
    {notice && <p className={['uncertain', 'conflict'].includes(notice) ? 'form-error' : 'form-success'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {error && <>{knownError ? <p className="form-error" role="alert">{t(`error_${error.code}`)}</p> : <ErrorNotice error={error} />}{data && <p className="quiet-note">{t('stale')}</p>}</>}
    {data && <><section className="computer-host" aria-labelledby="computer-host-heading"><h3 id="computer-host-heading">{t('hostTitle')}</h3><p className="computer-status">{t(`host_${data.host.status}`)}</p><p>{t(`driver_${data.host.driver}`)}</p><p className="quiet-note">{t('hostDescription')}</p></section>
      <div className="tools-section-heading"><h3>{t('plans')}</h3></div>{data.environments.length === 0 && <div className="tool-empty"><p>{t('empty')}</p><p>{t('emptyHelp')}</p></div>}
      <div className="computer-list">{data.environments.map((environment) => <article className="computer-card" key={environment.id}><div className="computer-card-heading"><h4>{environment.name}</h4><span className="computer-status">{t(`status_${environment.status}`)}</span></div><p>{t('resources', { cpu: environment.cpu_count, memory: environment.memory_mib, disk: environment.disk_gib })}</p><p className="quiet-note">{i18n.exists(`reason_${environment.reason_code}`, { ns: 'environments' }) ? t(`reason_${environment.reason_code}`) : t('noReason')}</p><p className="quiet-note">{t('created', { date: new Date(environment.created_at).toLocaleString(i18n.resolvedLanguage) })}</p>{environment.status === 'outcome_unknown' && <p className="form-error">{t('unknown')}</p>}{environment.status === 'awaiting_host' && <div className="tool-actions"><button type="button" className="button secondary" disabled={locked} onClick={() => { cancellationId.current = crypto.randomUUID(); setCancelling(environment); setNotice(null) }}>{t('cancelPlan')}</button></div>}</article>)}</div>
      {cancelling && <section className="tool-confirmation" aria-label={t('confirmCancel')}><p>{t('cancelImpact', { name: cancelling.name })}</p><div className="tool-actions"><button className="button secondary" type="button" disabled={busy} onClick={() => setCancelling(null)}>{t('keepPlan')}</button><button className="button primary" type="button" disabled={loading || busy || error !== null} onClick={cancel}>{t(busy ? 'saving' : 'confirmCancel')}</button></div></section>}
      {data.environments.some((environment) => environment.status === 'awaiting_host') && <p className="quiet-note computer-active-plan">{t('activePlan')}</p>}
      {data.templates[0] ? <EnvironmentForm key={`${data.templates[0].id}:${data.templates[0].version}`} template={data.templates[0]} agentName={agent.name} disabled={locked || data.environments.some((environment) => environment.status !== 'cancelled')} busy={busy} onSave={save} /> : <p className="quiet-note">{t('catalogEmpty')}</p>}
    </>}
  </section>
}
