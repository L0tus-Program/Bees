import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { saveConfiguration } from '../../api/bee'
import type { AgentSummary, Onboarding } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { ModelFields } from '../model/ModelFields'
import { useModelConnection } from '../model/useModelConnection'

export function ModelEditor({ agent, vault, onSaved, onReload }: { agent: AgentSummary; vault: Onboarding['vault']; onSaved: () => void; onReload: () => void }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const connection = useModelConnection(agent.provider_config, agent.id)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !connection.validated) return
    setBusy(true); setError(null); controller.current = new AbortController()
    try {
      await saveConfiguration(agent.id, agent.revision, connection.config, connection.validated.validation_token, connection.apiKey, controller.current.signal)
      if (!controller.current.signal.aborted) { connection.clear(); onSaved() }
    } catch (failure) {
      if (!controller.current.signal.aborted) { connection.invalidate(); setError(asApiError(failure)) }
    } finally { setBusy(false) }
  }

  const displayedError = error ?? connection.error
  return <section className="surface model-editor" aria-labelledby="model-editor-heading">
    <h2 id="model-editor-heading">{bee('modelTitle')}</h2><p className="form-introduction">{bee('modelDescription')}</p>
    {displayedError && <><ErrorNotice error={displayedError} /><button type="button" className="text-button" onClick={onReload}>{bee('reloadCurrent')}</button></>}
    <form onSubmit={(event) => { setError(null); void connection.check(event) }}>
      <fieldset disabled={busy || connection.testing}><ModelFields connection={connection} vault={vault} /><button type="submit" className="button secondary wide">{t(connection.testing ? 'testing' : 'testConnection')}</button></fieldset>
      <p className="quiet-note">{t('testScope')}</p>
    </form>
    {connection.validated && <p className="form-success" role="status">{bee('modelValidated')}</p>}
    <form onSubmit={(event) => { void submit(event) }}>
      <p className="transfer-notice">{bee('transferNotice')}</p>
      {agent.provider_config?.secret_ref && !connection.config.secret_ref && !connection.apiKey && <p className="transfer-notice">{bee('credentialRemoved')}</p>}
      <button type="submit" className="button primary wide" disabled={busy || connection.testing || !connection.validated}>{busy ? t('working') : bee('saveModel')}</button>
    </form>
  </section>
}
