import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { configurationSignature, testModel } from '../../api/onboarding'
import type { Diagnostic, ModelConfig } from '../../api/onboarding'
import { discoverModels, getProviders } from '../../api/providers'
import type { AvailableModel } from '../../api/providers'
import { useResource } from '../../hooks/useResource'
import { canReuseSecret, connectionConfig, inferProvider, modelOptions } from './connection'
import { createRequestGate } from './requestGate'

type Discovery = { state: 'idle' | 'loading' } | { state: 'ready'; models: AvailableModel[] } | { state: 'error'; error: ApiError }

export function useModelConnection(initial?: ModelConfig | null, agentId?: string) {
  const catalog = useResource(getProviders)
  const [providerId, setProviderId] = useState<string | null>(null)
  const [kind, setKind] = useState<ModelConfig['kind']>(initial?.kind ?? 'openai_compatible')
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [manual, setManual] = useState(false)
  const [tools, setTools] = useState(initial?.capabilities.tool_calls ?? false)
  const [apiKey, setApiKey] = useState('')
  const [reuse, setReuse] = useState(false)
  const [receipt, setReceipt] = useState<{ signature: string; diagnostic: Diagnostic } | null>(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [discovery, setDiscovery] = useState<Discovery>({ state: 'idle' })
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
  const config = connectionConfig(initial, { kind, endpoint, model, tools, reuse })
  const signature = configurationSignature(config, apiKey)
  const discovering = discovery.state === 'loading'
  const credentialReady = !provider?.requires_api_key || !!apiKey || !!config.secret_ref

  function invalidate() { setReceipt(null); setError(null) }
  function cancelRequests() { gate.cancel(); running.current = false; setTesting(false); setDiscovery((current) => current.state === 'loading' ? { state: 'idle' } : current) }
  function changeDiscoveryContext(resetModel = false) { cancelRequests(); invalidate(); setDiscovery({ state: 'idle' }); if (resetModel) setModel('') }

  async function discover() {
    if (running.current || !provider || !config.endpoint || !credentialReady) return
    running.current = true
    const controller = gate.begin()
    setDiscovery({ state: 'loading' }); invalidate()
    try {
      const models = await discoverModels({ provider_id: provider.id, ...(!provider.endpoint ? { endpoint: config.endpoint } : {}),
        ...(apiKey ? { api_key: apiKey } : {}), ...(agentId ? { agent_id: agentId } : {}),
        ...(config.secret_ref ? { secret_ref: config.secret_ref } : {}),
      }, controller.signal)
      if (gate.current(controller)) setDiscovery({ state: 'ready', models })
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
      const diagnostic = await testModel(config, apiKey, controller.signal, agentId)
      if (gate.current(controller)) setReceipt({ signature, diagnostic })
    } catch (failure) { if (gate.current(controller)) setError(asApiError(failure)) }
    finally { if (gate.current(controller)) { running.current = false; setTesting(false) } }
  }

  return {
    kind, endpoint, model, tools, apiKey, reuse, config, error, testing, check, provider, catalog, discovery, discovering, discover, manual,
    options: modelOptions(discovery.state === 'ready' ? discovery.models : [], model),
    canDiscover: !!provider && !!config.endpoint && credentialReady,
    canTest: !!provider && !!config.model && !!config.endpoint && credentialReady && !discovering,
    canReuse: canReuseSecret(initial, config), validated: receipt?.signature === signature ? receipt.diagnostic : null,
    invalidate,
    clear: () => { changeDiscoveryContext(true); setApiKey(''); setReuse(false) },
    changeProvider: (id: string) => {
      const next = providers.find((item) => item.id === id)
      if (!next) return
      changeDiscoveryContext(true); setProviderId(id); setKind(next.kind); setEndpoint(next.endpoint); setApiKey(''); setReuse(false); setTools(false); setManual(false)
    },
    changeEndpoint: (value: string) => { changeDiscoveryContext(true); setEndpoint(value); setApiKey(''); setReuse(false) },
    changeModel: (value: string) => { cancelRequests(); invalidate(); setModel(value) },
    changeTools: (value: boolean) => { cancelRequests(); invalidate(); setTools(value) },
    changeKey: (value: string) => { changeDiscoveryContext(); setApiKey(value); setReuse(false) },
    changeReuse: (value: boolean) => { changeDiscoveryContext(); setReuse(value); setApiKey('') },
    changeManual: (value: boolean) => { cancelRequests(); invalidate(); setManual(value) },
  }
}
