import { useTranslation } from 'react-i18next'
import type { Onboarding } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import type { useModelConnection } from './useModelConnection'

export function ModelFields({ connection, vault }: { connection: ReturnType<typeof useModelConnection>; vault: Onboarding['vault'] }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const { provider, catalog, discovery } = connection
  const custom = !!provider && !provider.endpoint
  return (
    <>
      {catalog.resource.state === 'error' && <><ErrorNotice error={catalog.resource.error} /><button type="button" className="text-button" onClick={catalog.reload}>{t('retry')}</button></>}
      <label className="field" htmlFor="provider-kind"><span>{t('provider')}</span><select id="provider-kind" required disabled={catalog.resource.state !== 'ready'} value={provider?.id ?? ''} onChange={(event) => connection.changeProvider(event.target.value)}>
        <option value="" disabled>{t(catalog.resource.state === 'loading' ? 'loadingProviders' : 'chooseProvider')}</option>
        {catalog.resource.state === 'ready' && catalog.resource.data.map((item) => <option key={item.id} value={item.id}>{item.endpoint ? item.name : t(item.kind === 'ollama' ? 'customOllama' : 'customProvider')}</option>)}
      </select></label>
      {custom && <label className="field" htmlFor="model-endpoint"><span>{t('endpoint')} <span className="optional">{t('advancedConnection')}</span></span><input id="model-endpoint" type="url" value={connection.endpoint} onChange={(event) => connection.changeEndpoint(event.target.value)} required maxLength={2048} placeholder={connection.kind === 'ollama' ? 'http://127.0.0.1:11434' : 'https://api.example.com/v1'} autoComplete="off" aria-describedby="endpoint-help" /><small id="endpoint-help">{t(connection.kind === 'ollama' ? 'localEndpointHelp' : 'remoteEndpointHelp')}</small></label>}
      {provider?.kind === 'ollama' && !custom && <p className="quiet-note">{t('localEndpointHelp')}</p>}
      {provider?.kind === 'openai_compatible' && <>
        {connection.canReuse && <label className="checkbox-field"><input type="checkbox" checked={connection.reuse} onChange={(event) => connection.changeReuse(event.target.checked)} /><span>{bee('reuseCredential')}<small>{bee('reuseCredentialHelp')}</small></span></label>}
        <label className="field" htmlFor="api-key"><span>{t('apiKey')} {!provider.requires_api_key && <span className="optional">{t('optional')}</span>}</span><input id="api-key" type="password" autoComplete="off" value={connection.apiKey} disabled={connection.reuse} onChange={(event) => connection.changeKey(event.target.value)} maxLength={8192} aria-describedby="key-help" /><small id="key-help">{connection.reuse ? bee('credentialReused') : t(vault.available ? 'keyHelp' : 'vaultUnavailable')}</small></label>
      </>}
      {provider && <>
        <button type="button" className="button secondary wide" disabled={connection.testing || connection.discovering || !connection.canDiscover} onClick={() => { void connection.discover() }}>{t(connection.discovering ? 'discoveringModels' : discovery.state === 'error' ? 'retryModels' : 'discoverModels')}</button>
        <p className="quiet-note">{t('discoveryScope')}</p>
        <div aria-live="polite">{discovery.state === 'error' && <ErrorNotice error={discovery.error} />}{discovery.state === 'ready' && !discovery.models.length && <p className="transfer-notice">{t('noModels')}</p>}</div>
        {!connection.manual && <label className="field" htmlFor="model-id"><span>{t('model')}</span><select id="model-id" value={connection.model} onChange={(event) => connection.changeModel(event.target.value)} required disabled={connection.discovering || !connection.options.length} aria-describedby="model-list-help"><option value="" disabled>{t('chooseModel')}</option>{connection.options.map((item) => <option key={item.id} value={item.id}>{item.name === item.id ? item.id : `${item.name} · ${item.id}`}</option>)}</select><small id="model-list-help">{t(discovery.state === 'ready' && discovery.models.length ? 'modelListReadyHelp' : connection.model && discovery.state === 'idle' ? 'currentModelPreserved' : 'modelListHelp')}</small></label>}
        <details className="advanced-setup"><summary>{t('advancedModelOptions')}</summary>
          {custom && <>
            <label className="checkbox-field"><input type="checkbox" checked={connection.manual} onChange={(event) => connection.changeManual(event.target.checked)} /><span>{t('manualModel')}<small>{t('manualModelHelp')}</small></span></label>
            {connection.manual && <label className="field" htmlFor="manual-model-id"><span>{t('model')}</span><input id="manual-model-id" value={connection.model} onChange={(event) => connection.changeModel(event.target.value)} required maxLength={200} placeholder={t('modelPlaceholder')} autoComplete="off" /></label>}
          </>}
          <label className="checkbox-field"><input type="checkbox" checked={connection.tools} onChange={(event) => connection.changeTools(event.target.checked)} /><span>{t('toolCalls')}<small>{t('toolCallsHelp')}</small></span></label>
        </details>
      </>}
    </>
  )
}
