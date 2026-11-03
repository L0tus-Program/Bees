import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { configurationSignature, testModel } from '../../api/onboarding'
import type { Diagnostic, ModelConfig } from '../../api/onboarding'
import { discoverModels, getModelCredentials, getProviders } from '../../api/providers'
import type { AvailableModel } from '../../api/providers'
import { useResource } from '../../hooks/useResource'
import { canReuseSecret, canSaveModel, connectionConfig, inferProvider, mergeModels, modelOptions, normalizeEndpoint } from './connection'
import { createRequestGate } from './requestGate'

type Discovery = { state: 'idle' | 'loading' } | { state: 'ready'; models: AvailableModel[] } | { state: 'error'; error: ApiError }

export function useModelConnection(initial?: ModelConfig | null, agentId?: string) {
  const catalog = useResource(getProviders)
  const credentials = useResource(getModelCredentials)
  const [credentialId, setCredentialId] = useState('')
  const [providerId, setProviderId] = useState<string | null>(null)
  const [kind, setKind] = useState<ModelConfig['kind']>(initial?.kind ?? 'openai_compatible')
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [api, setApi] = useState<NonNullable<ModelConfig['api']>>(initial?.api ?? 'auto')
  const [manual, setManual] = useState(false)
  const [tools, setTools] = useState(initial?.capabilities.tool_calls ?? false)
  const [vision, setVision] = useState(initial?.capabilities.vision ?? false)
  const [apiKey, setApiKey] = useState('')
  const [reuse, setReuse] = useState(false)
  const [receipt, setReceipt] = useState<{ signature: string; diagnostic: Diagnostic } | null>(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [discovery, setDiscovery] = useState<Discovery>({ state: 'idle' })
  const [discoveredModels, setDiscoveredModels] = useState<AvailableModel[]>([])
  const [gate] = useState(createRequestGate)
  const running = useRef(false)
  useEffect(() => () => { gate.cancel(); running.current = false }, [gate])
  useEffect(() => {
    if (!receipt) return
    const timeout = setTimeout(() => setReceipt(null), 300000)
    return () => clearTimeout(timeout)
  }, [receipt])
  const providers = catalog.resource.state === 'ready' ? catalog.resource.data : []
  const provider = providerId === null ? inferProvider(providers, initial) : providers.find((item) => item.id === providerId)
  const config = connectionConfig(initial, { kind, endpoint, model, tools, reuse, vision, api })
  const savedCredentials = credentials.resource.state === 'ready' ? credentials.resource.data.filter((item) => item.kind === config.kind && normalizeEndpoint(item.endpoint) === config.endpoint) : []
  const selectedCredentialId = savedCredentials.some((item) => item.id === credentialId) ? credentialId : ''
  const signature = configurationSignature(config, apiKey) + selectedCredentialId
  const discovering = discovery.state === 'loading'
  const credentialReady = !provider?.requires_api_key || !!apiKey || !!config.secret_ref || !!selectedCredentialId

  function invalidate() { setReceipt(null); setError(null) }
  function cancelRequests() { gate.cancel(); running.current = false; setTesting(false); setDiscovery((current) => current.state === 'loading' ? { state: 'idle' } : current) }
  function changeDiscoveryContext(resetModel = false) { cancelRequests(); invalidate(); setDiscovery({ state: 'idle' }); setDiscoveredModels([]); if (resetModel) setModel('') }

  async function discover() {
    if (running.current || !provider || !config.endpoint || !credentialReady) return
    running.current = true
    const controller = gate.begin()
    setDiscovery({ state: 'loading' })
    try {
      const models = await discoverModels({ provider_id: provider.id, ...(!provider.endpoint ? { endpoint: config.endpoint } : {}),
        ...(apiKey ? { api_key: apiKey } : {}), ...(agentId ? { agent_id: agentId } : {}),
        ...(config.secret_ref ? { secret_ref: config.secret_ref } : {}), ...(selectedCredentialId ? { credential_id: selectedCredentialId } : {}),
      }, controller.signal)
      if (gate.current(controller)) { setDiscoveredModels((current) => mergeModels(current, models)); setDiscovery({ state: 'ready', models }) }
    } catch (failure) { if (gate.current(controller)) setDiscovery({ state: 'error', error: asApiError(failure) }) }
    finally { if (gate.current(controller)) running.current = false }
  }

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (running.current || !provider || !config.model || !credentialReady) return
    running.current = true
    invalidate(); setTesting(true)
    const controller = gate.begin()
    try {
      const diagnostic = await testModel(config, apiKey, controller.signal, agentId, selectedCredentialId)
      if (gate.current(controller)) setReceipt({ signature, diagnostic })
    } catch (failure) { if (gate.current(controller)) setError(asApiError(failure)) }
    finally { if (gate.current(controller)) { running.current = false; setTesting(false) } }
  }

  return {
    kind, endpoint, model, tools, vision, api, apiKey, reuse, config, credentials, savedCredentials, credentialId: selectedCredentialId, error, testing, check, provider, catalog, discovery, discovering, discover, manual,
    options: modelOptions(mergeModels(provider?.models ?? [], discoveredModels), model),
    canDiscover: !!provider && !!config.endpoint && credentialReady,
    canTest: !!provider && !!config.model && !!config.endpoint && credentialReady && !discovering,
    canReuse: canReuseSecret(initial, config), validated: receipt?.signature === signature ? receipt.diagnostic : null,
    canSave: (vaultAvailable: boolean) => canSaveModel(provider, config, apiKey, vaultAvailable, selectedCredentialId),
    beginSave: () => { cancelRequests(); return gate.begin() },
    invalidate,
    clear: () => { changeDiscoveryContext(true); setApiKey(''); setReuse(false); setCredentialId('') },
    changeProvider: (id: string) => {
      const next = providers.find((item) => item.id === id)
      if (!next) return
      changeDiscoveryContext(true); setProviderId(id); setKind(next.kind); setEndpoint(next.endpoint); setApi('auto'); setApiKey(''); setReuse(false); setCredentialId(''); setTools(false); setVision(false); setManual(false)
    },
    changeEndpoint: (value: string) => { changeDiscoveryContext(true); setEndpoint(value); setApi('auto'); setApiKey(''); setReuse(false); setCredentialId('') },
    changeApi: (value: NonNullable<ModelConfig['api']>) => { cancelRequests(); invalidate(); setApi(value) },
    changeModel: (value: string) => { cancelRequests(); invalidate(); setModel(value) },
    changeVision: (value: boolean) => { cancelRequests(); invalidate(); setVision(value) },
    changeTools: (value: boolean) => { cancelRequests(); invalidate(); setTools(value) },
    changeKey: (value: string) => { changeDiscoveryContext(); setApiKey(value); setReuse(false); setCredentialId('') },
    changeReuse: (value: boolean) => { changeDiscoveryContext(); setReuse(value); setApiKey(''); setCredentialId('') },
    changeCredential: (id: string) => { changeDiscoveryContext(); setCredentialId(id); setApiKey(''); setReuse(false) },
    changeManual: (value: boolean) => { cancelRequests(); invalidate(); setManual(value) },
  }
}
