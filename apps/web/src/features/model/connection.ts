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
