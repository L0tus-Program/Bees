import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { configurationSignature, createAgent, testModel } from '../../api/onboarding'
import type { AgentSummary, Diagnostic, ModelConfig, Onboarding } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'

export function CreateBee({ vault, onCreated, onCancel, onReconcile }: { vault: Onboarding['vault']; onCreated: (agent: AgentSummary) => void; onCancel?: () => void; onReconcile: () => void }) {
  const { t } = useTranslation('product')
  const [kind, setKind] = useState<ModelConfig['kind']>('openai_compatible')
  const [endpoint, setEndpoint] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [tools, setTools] = useState(false)
  const [name, setName] = useState('')
  const [purpose, setPurpose] = useState('')
  const [instructions, setInstructions] = useState('')
  const [validated, setValidated] = useState<{ signature: string; diagnostic: Diagnostic } | null>(null)
  const [busy, setBusy] = useState<'test' | 'create' | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  const profileRef = useRef<HTMLInputElement>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const config: ModelConfig = { kind, endpoint: endpoint.trim(), model: model.trim(), capabilities: { text: true, tool_calls: tools } }
  const signature = configurationSignature(config, apiKey)
  const tested = validated?.signature === signature
  useEffect(() => { if (tested) profileRef.current?.focus() }, [tested])

  function edit(change: () => void) { change(); setValidated(null); setError(null) }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy('test'); setError(null); setValidated(null)
    controller.current = new AbortController()
    try {
      const diagnostic = await testModel(config, apiKey, controller.current.signal)
      setValidated({ signature, diagnostic })
    } catch (failure) {
      if (!controller.current.signal.aborted) setError(asApiError(failure))
    } finally { setBusy(null) }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !tested || !validated) return
    setBusy('create'); setError(null)
    controller.current = new AbortController()
    try {
      const agent = await createAgent({ name: name.trim(), purpose: purpose.trim(), instructions: instructions.trim(), config, validation_token: validated.diagnostic.validation_token, ...(apiKey ? { api_key: apiKey } : {}) }, controller.current.signal)
      setApiKey(''); setValidated(null)
      onCreated(agent)
    } catch (failure) {
      if (!controller.current.signal.aborted) {
        const apiError = asApiError(failure)
        if (apiError.code.startsWith('validation_') || apiError.code.includes('test_required') || ['mutation_network', 'mutation_timeout', 'invalid_response'].includes(apiError.code)) setValidated(null)
        setError(apiError)
      }
    } finally { setBusy(null) }
  }

  return (
    <section className="setup-flow" aria-labelledby="setup-heading">
      <div className="page-heading"><div><p className="eyebrow">{t('firstBeeEyebrow')}</p><h1 id="setup-heading">{t('firstBeeTitle')}</h1><p>{t('firstBeeDescription')}</p></div>{onCancel && <button type="button" className="text-button" onClick={onCancel} disabled={!!busy}>{t('back')}</button>}</div>
      {error && <><ErrorNotice error={error} />{['mutation_network', 'mutation_timeout', 'invalid_response'].includes(error.code) && <button type="button" className="button secondary" onClick={onReconcile}>{t('refreshState')}</button>}</>}
      <div className="setup-columns">
        <form className="surface" onSubmit={(event) => { void check(event) }}>
          <div className="number-heading"><span>01</span><h2>{t('connectModel')}</h2></div>
          <p className="form-introduction">{t('modelDescription')}</p>
          <fieldset disabled={!!busy}>
            <label className="field" htmlFor="provider-kind"><span>{t('provider')}</span><select id="provider-kind" value={kind} onChange={(event) => edit(() => { const nextKind = event.target.value as ModelConfig['kind']; setKind(nextKind); setEndpoint(nextKind === 'ollama' ? 'http://127.0.0.1:11434' : ''); setApiKey(''); setTools(false) })}><option value="openai_compatible">{t('remoteProvider')}</option><option value="ollama">{t('localProvider')}</option></select></label>
            <label className="field" htmlFor="model-endpoint"><span>{t('endpoint')}</span><input id="model-endpoint" type="url" value={endpoint} onChange={(event) => edit(() => setEndpoint(event.target.value))} required maxLength={2048} placeholder={kind === 'ollama' ? 'http://127.0.0.1:11434' : 'https://api.example.com/v1'} autoComplete="off" aria-describedby="endpoint-help" /><small id="endpoint-help">{t(kind === 'ollama' ? 'localEndpointHelp' : 'remoteEndpointHelp')}</small></label>
            <label className="field" htmlFor="model-id"><span>{t('model')}</span><input id="model-id" value={model} onChange={(event) => edit(() => setModel(event.target.value))} required maxLength={200} placeholder={t('modelPlaceholder')} autoComplete="off" /></label>
            {kind === 'openai_compatible' && <label className="field" htmlFor="api-key"><span>{t('apiKey')} <span className="optional">{t('optional')}</span></span><input id="api-key" type="password" autoComplete="off" value={apiKey} disabled={!vault.available} onChange={(event) => edit(() => setApiKey(event.target.value))} maxLength={8192} aria-describedby="key-help" /><small id="key-help">{t(vault.available ? 'keyHelp' : 'vaultUnavailable')}</small></label>}
            <label className="checkbox-field"><input type="checkbox" checked={tools} onChange={(event) => edit(() => setTools(event.target.checked))} /><span>{t('toolCalls')}<small>{t('toolCallsHelp')}</small></span></label>
            <button type="submit" className="button secondary wide">{t(busy === 'test' ? 'testing' : 'testConnection')}</button>
          </fieldset>
          <p className="quiet-note">{t('testScope')}</p>
          {tested && <p className="form-success" role="status">{t('modelTested')}</p>}
        </form>
        <form className={`surface profile-form ${tested ? 'profile-ready' : ''}`} onSubmit={(event) => { void save(event) }}>
          <div className="number-heading"><span>02</span><h2>{t('givePurpose')}</h2></div>
          <p className="form-introduction">{t('profileDescription')}</p>
          <fieldset disabled={!!busy}>
            <label className="field" htmlFor="bee-name"><span>{t('beeName')}</span><input id="bee-name" ref={profileRef} value={name} onChange={(event) => setName(event.target.value)} required maxLength={100} placeholder={t('beeNamePlaceholder')} autoComplete="off" /></label>
            <label className="field" htmlFor="bee-purpose"><span>{t('purpose')}</span><textarea id="bee-purpose" rows={3} value={purpose} onChange={(event) => setPurpose(event.target.value)} required maxLength={2000} placeholder={t('purposePlaceholder')} /></label>
            <label className="field" htmlFor="bee-instructions"><span>{t('instructions')} <span className="optional">{t('optional')}</span></span><textarea id="bee-instructions" rows={4} value={instructions} onChange={(event) => setInstructions(event.target.value)} maxLength={10000} placeholder={t('instructionsPlaceholder')} /><small>{t('instructionsHelp')}</small></label>
            <button type="submit" className="button primary wide" disabled={!tested}>{t(busy === 'create' ? 'creating' : 'createBee')}</button>
          </fieldset>
          {!tested && <p className="quiet-note">{t('testBeforeCreate')}</p>}
          <p className="quiet-note">{t('noMachineNeeded')}</p>
        </form>
      </div>
    </section>
  )
}
