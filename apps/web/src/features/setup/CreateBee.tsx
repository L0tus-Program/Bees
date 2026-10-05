import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createAgent } from '../../api/onboarding'
import type { AgentSummary, Onboarding } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { ModelFields } from '../model/ModelFields'
import { useModelConnection } from '../model/useModelConnection'
import { preparedSave } from '../model/preparedSave'

export function CreateBee({ vault, onCreated, onCancel, onReconcile }: { vault: Onboarding['vault']; onCreated: (agent: AgentSummary) => void; onCancel?: () => void; onReconcile: () => void }) {
  const { t } = useTranslation('product')
  const connection = useModelConnection()
  const [name, setName] = useState('')
  const [purpose, setPurpose] = useState('')
  const [instructions, setInstructions] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  const tested = !!connection.validated
  const canCreate = connection.canSave(vault.available) && !!name.trim() && !!purpose.trim()
  useEffect(() => () => controller.current?.abort(), [])

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !canCreate) return
    setBusy(true); setError(null)
    controller.current = connection.beginSave()
    const operation = controller.current
    try {
      const agent = await preparedSave(connection.config, connection.apiKey, operation.signal, (validationToken) => createAgent({ name: name.trim(), purpose: purpose.trim(), instructions: instructions.trim(), config: connection.config, validation_token: validationToken, ...(connection.apiKey ? { api_key: connection.apiKey } : {}) }, operation.signal))
      if (!operation.signal.aborted) { connection.clear(); onCreated(agent) }
    } catch (failure) {
      if (!operation.signal.aborted) {
        const apiError = asApiError(failure)
        if (apiError.code.startsWith('validation_') || apiError.code.includes('test_required') || ['mutation_network', 'mutation_timeout', 'invalid_response'].includes(apiError.code)) connection.invalidate()
        setError(apiError)
      }
    } finally { setBusy(false) }
  }

  const displayedError = error ?? connection.error
  return (
    <section className="setup-flow" aria-labelledby="setup-heading">
      <div className="page-heading"><div><p className="eyebrow">{t('firstBeeEyebrow')}</p><h1 id="setup-heading">{t('firstBeeTitle')}</h1><p>{t('firstBeeDescription')}</p></div>{onCancel && <button type="button" className="text-button" onClick={onCancel} disabled={busy || connection.testing}>{t('back')}</button>}</div>
      {displayedError && <><ErrorNotice error={displayedError} />{['mutation_network', 'mutation_timeout', 'invalid_response'].includes(displayedError.code) && <button type="button" className="button secondary" onClick={onReconcile}>{t('refreshState')}</button>}</>}
      <div className="setup-columns">
        <form className="surface" onSubmit={(event) => { if (connection.apiKey && !vault.available) { event.preventDefault(); return }; setError(null); void connection.check(event) }}>
          <div className="number-heading"><span>01</span><h2>{t('connectModel')}</h2></div>
          <p className="form-introduction">{t('modelDescription')}</p>
          <fieldset disabled={busy || connection.testing}>
            <ModelFields connection={connection} vault={vault} />
            <button type="submit" className="button secondary wide" disabled={!connection.canTest || (!!connection.apiKey && !vault.available)}>{t(connection.testing ? 'testing' : 'testConnection')}</button>
          </fieldset>
          <p className="quiet-note">{t('testScope')}</p>
          {tested && <p className="form-success" role="status">{t('modelTested')}</p>}
        </form>
        <form className="surface profile-form profile-ready" onSubmit={(event) => { void save(event) }}>
          <div className="number-heading"><span>02</span><h2>{t('givePurpose')}</h2></div>
          <p className="form-introduction">{t('profileDescription')}</p>
          <fieldset disabled={busy}>
            <label className="field" htmlFor="bee-name"><span>{t('beeName')}</span><input id="bee-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} placeholder={t('beeNamePlaceholder')} autoComplete="off" /></label>
            <label className="field" htmlFor="bee-purpose"><span>{t('purpose')}</span><textarea id="bee-purpose" rows={3} value={purpose} onChange={(event) => setPurpose(event.target.value)} required maxLength={2000} placeholder={t('purposePlaceholder')} /></label>
            <label className="field" htmlFor="bee-instructions"><span>{t('instructions')} <span className="optional">{t('optional')}</span></span><textarea id="bee-instructions" rows={4} value={instructions} onChange={(event) => setInstructions(event.target.value)} maxLength={10000} placeholder={t('instructionsPlaceholder')} /><small>{t('instructionsHelp')}</small></label>
            <button type="submit" className="button primary wide" disabled={!canCreate}>{t(busy ? 'creating' : 'createBee')}</button>
          </fieldset>
          <p className="quiet-note">{t('saveWithoutTest')}</p>
          <p className="quiet-note">{t('noMachineNeeded')}</p>
        </form>
      </div>
    </section>
  )
}
