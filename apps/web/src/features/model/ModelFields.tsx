import { useTranslation } from 'react-i18next'
import type { ModelConfig, Onboarding } from '../../api/onboarding'
import type { useModelConnection } from './useModelConnection'

export function ModelFields({ connection, vault }: { connection: ReturnType<typeof useModelConnection>; vault: Onboarding['vault'] }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  return (
    <>
      <label className="field" htmlFor="provider-kind"><span>{t('provider')}</span><select id="provider-kind" value={connection.kind} onChange={(event) => connection.changeKind(event.target.value as ModelConfig['kind'])}><option value="openai_compatible">{t('remoteProvider')}</option><option value="ollama">{t('localProvider')}</option></select></label>
      <label className="field" htmlFor="model-endpoint"><span>{t('endpoint')}</span><input id="model-endpoint" type="url" value={connection.endpoint} onChange={(event) => connection.changeEndpoint(event.target.value)} required maxLength={2048} placeholder={connection.kind === 'ollama' ? 'http://127.0.0.1:11434' : 'https://api.example.com/v1'} autoComplete="off" aria-describedby="endpoint-help" /><small id="endpoint-help">{t(connection.kind === 'ollama' ? 'localEndpointHelp' : 'remoteEndpointHelp')}</small></label>
      <label className="field" htmlFor="model-id"><span>{t('model')}</span><input id="model-id" value={connection.model} onChange={(event) => connection.changeModel(event.target.value)} required maxLength={200} placeholder={t('modelPlaceholder')} autoComplete="off" /></label>
      {connection.kind === 'openai_compatible' && <>
        {connection.canReuse && <label className="checkbox-field"><input type="checkbox" checked={connection.reuse} onChange={(event) => connection.changeReuse(event.target.checked)} /><span>{bee('reuseCredential')}<small>{bee('reuseCredentialHelp')}</small></span></label>}
        <label className="field" htmlFor="api-key"><span>{t('apiKey')} <span className="optional">{t('optional')}</span></span><input id="api-key" type="password" autoComplete="off" value={connection.apiKey} disabled={!vault.available || connection.reuse} onChange={(event) => connection.changeKey(event.target.value)} maxLength={8192} aria-describedby="key-help" /><small id="key-help">{connection.reuse ? bee('credentialReused') : t(vault.available ? 'keyHelp' : 'vaultUnavailable')}</small></label>
      </>}
      <label className="checkbox-field"><input type="checkbox" checked={connection.tools} onChange={(event) => connection.changeTools(event.target.checked)} /><span>{t('toolCalls')}<small>{t('toolCallsHelp')}</small></span></label>
    </>
  )
}
