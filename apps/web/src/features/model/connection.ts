import type { ModelConfig } from '../../api/onboarding'

export const normalizeEndpoint = (endpoint: string) => endpoint.trim().replace(/\/+$/, '')

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
