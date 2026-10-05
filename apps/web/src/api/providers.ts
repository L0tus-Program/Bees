import { ApiError, record, request } from './client'
import type { ModelConfig } from './onboarding'

export interface Provider { id: string; name: string; kind: ModelConfig['kind']; endpoint: string; requires_api_key: boolean }
export interface AvailableModel { id: string; name: string }
export interface DiscoveryRequest { provider_id: string; endpoint?: string; api_key?: string; agent_id?: string; secret_ref?: string }

export async function getProviders(signal?: AbortSignal): Promise<Provider[]> {
  const value = await request('/providers', { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.providers) || !value.providers.length
    || !value.providers.every((item) => record(item) && typeof item.id === 'string' && !!item.id
      && typeof item.name === 'string' && !!item.name && ['openai_compatible', 'ollama'].includes(String(item.kind))
      && typeof item.endpoint === 'string' && typeof item.requires_api_key === 'boolean')
    || new Set(value.providers.map((item) => item.id)).size !== value.providers.length) throw new ApiError('invalid_response')
  return value.providers.map((item) => ({ id: item.id, name: item.name, kind: item.kind, endpoint: item.endpoint, requires_api_key: item.requires_api_key })) as Provider[]
}

export async function discoverModels(body: DiscoveryRequest, signal?: AbortSignal): Promise<AvailableModel[]> {
  const value = await request('/models/discover', { body, authenticated: true, signal, timeoutMs: 40000 })
  if (!record(value) || value.provider_id !== body.provider_id || !Array.isArray(value.models)
    || !value.models.every((item) => record(item) && typeof item.id === 'string' && !!item.id
      && typeof item.name === 'string' && !!item.name)
    || new Set(value.models.map((item) => item.id)).size !== value.models.length) throw new ApiError('invalid_response')
  // Keep only display fields; credentials and provider payloads never become UI state.
  return value.models.map((item) => ({ id: item.id, name: item.name }))
}
