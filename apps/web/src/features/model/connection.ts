import type { ModelConfig } from '../../api/onboarding'
import type { AvailableModel, Provider } from '../../api/providers'

export const normalizeEndpoint = (endpoint: string) => endpoint.trim().replace(/\/+$/, '')

export function inferProvider(providers: Provider[], config?: ModelConfig | null) {
  if (!config) return undefined
  return providers.find((item) => item.endpoint && item.kind === config.kind && normalizeEndpoint(item.endpoint) === normalizeEndpoint(config.endpoint))
    ?? providers.find((item) => !item.endpoint && item.kind === config.kind)
}

export function modelOptions(models: AvailableModel[], selected: string) {
  return selected && !models.some((item) => item.id === selected) ? [{ id: selected, name: selected }, ...models] : models
}

export function mergeModels(suggested: AvailableModel[], discovered: AvailableModel[]) {
  const models = new Map(suggested.map((model) => [model.id, model]))
  for (const model of discovered) models.set(model.id, model)
  return [...models.values()]
}

export function canSaveModel(provider: Provider | undefined, config: ModelConfig, apiKey: string, vaultAvailable: boolean) {
  if (!provider || !config.model.trim() || config.model.trim().length > 200 || !config.endpoint.trim()) return false
  try {
    const endpoint = new URL(config.endpoint)
    if (!['https:', 'http:'].includes(endpoint.protocol) || endpoint.username || endpoint.password) return false
  } catch { return false }
  return (!provider.requires_api_key || !!apiKey || !!config.secret_ref) && (!apiKey || vaultAvailable)
}

export function canReuseSecret(initial: ModelConfig | null | undefined, config: Pick<ModelConfig, 'kind' | 'endpoint'>) {
  return !!initial?.secret_ref && initial.kind === config.kind && normalizeEndpoint(initial.endpoint) === normalizeEndpoint(config.endpoint)
}

export function connectionConfig(initial: ModelConfig | null | undefined, values: { kind: ModelConfig['kind']; endpoint: string; model: string; tools: boolean; reuse: boolean }): ModelConfig {
  const { secret_ref: _secret, ...base } = initial ?? { kind: 'openai_compatible', endpoint: '', model: '', capabilities: { text: true, tool_calls: false } }
  void _secret
  const config: ModelConfig = { ...base, kind: values.kind, endpoint: normalizeEndpoint(values.endpoint), model: values.model.trim(), capabilities: { text: true, tool_calls: values.tools } }
  if (values.reuse && canReuseSecret(initial, config)) config.secret_ref = initial!.secret_ref
  return config
}
