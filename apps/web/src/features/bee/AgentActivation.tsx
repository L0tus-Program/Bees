import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { reactivateAgent } from '../../api/activation'
import { onboarding } from '../../api/onboarding'
import type { AgentSummary } from '../../api/onboarding'
import { getSafety } from '../../api/safety'

export function AgentActivation({ agent, onSaved, onReload }: { agent: AgentSummary; onSaved: () => void; onReload: () => void }) {
  const { t } = useTranslation('activation')
  const [prepared, setPrepared] = useState<{ safety: number } | null>(null)
  const [consent, setConsent] = useState(false), [busy, setBusy] = useState<'check' | 'activate' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const operation = useRef<AbortController | null>(null), locked = useRef(false)
  useEffect(() => () => { operation.current?.abort() }, [])
  function start(kind: 'check' | 'activate') {
    if (locked.current) return null
    locked.current = true; const controller = new AbortController(); operation.current = controller
    setBusy(kind); setError(null); setConsent(false); return controller
  }
  function finish(controller: AbortController) { if (!controller.signal.aborted) { locked.current = false; setBusy(null) } }
  async function check() {
    const controller = start('check'); if (!controller) return
    setPrepared(null)
    try {
      const [current, safety] = await Promise.all([onboarding(controller.signal), getSafety(controller.signal)])
      if (controller.signal.aborted) return
      const saved = current.agents.find(value => value.id === agent.id)
      if (saved?.status === 'active') { onSaved(); return }
      if (!saved || saved.status !== 'paused' || saved.revision !== agent.revision || !saved.provider_config) { setError('changed'); return }
      if (safety.status !== 'stopped') { setError('stopRequired'); return }
      setPrepared({ safety: safety.revision })
    } catch { if (!controller.signal.aborted) setError('checkFailure') }
    finally { finish(controller) }
  }
  async function activate() {
    if (!prepared || !consent) return
    const controller = start('activate'); if (!controller) return
    setPrepared(null)
    try {
      await reactivateAgent(agent.id, { expected_revision: agent.revision, expected_safety_revision: prepared.safety, consent: 'reactivate_paused_agent' }, controller.signal)
      if (!controller.signal.aborted) onSaved()
    } catch { if (!controller.signal.aborted) setError('failure') }
    finally { finish(controller) }
  }
  if (agent.status !== 'paused') return null
  return <section className="surface model-editor" aria-labelledby="agent-activation-heading" aria-busy={busy !== null}>
    <h2 id="agent-activation-heading">{t('title')}</h2><p className="form-introduction">{t('scope')}</p><p className="quiet-note">{t('saveFirst')}</p>
    {agent.provider_config && <dl className="task-definition"><div><dt>{t('saved')}</dt><dd>{agent.provider_config.model}<br /><code>{agent.provider_config.endpoint}</code></dd></div></dl>}
    {error && <><p className="form-error" role="status">{t(error)}</p><button type="button" className="text-button" disabled={busy !== null} onClick={onReload}>{t('reload')}</button></>}
    <button type="button" className="button secondary" disabled={!agent.provider_config || busy !== null} onClick={() => { void check() }}>{t(busy === 'check' ? 'checking' : 'check')}</button>
    {prepared && <p className="quiet-note" role="status">{t('ready')}</p>}
    <form onSubmit={event => { event.preventDefault(); void activate() }}><fieldset disabled={!prepared || busy !== null}><label className="checkbox-field"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>{t('consent')}</span></label><button type="submit" className="button primary" disabled={!consent}>{t(busy === 'activate' ? 'submitting' : 'submit')}</button></fieldset></form>
    <p className="transfer-notice">{t('limits')}</p>
  </section>
}
