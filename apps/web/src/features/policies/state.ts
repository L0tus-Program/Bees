import type { PolicyCollection, PolicyRecord, PolicyScope } from '../../api/policies'
import type { ModelConfig } from '../../api/onboarding'
import { record } from '../../api/client'

export function generationScope(config: ModelConfig | null, connectionOnly: boolean): PolicyScope {
  const scope: PolicyScope = { tool_name: 'model', action: 'generate', environment_id: 'control_plane' }
  if (connectionOnly) {
    if (!config) throw new Error('model_not_configured')
    scope.resource = config.endpoint
    scope.parameters = { model: config.model }
  }
  return scope
}
export function editableGenerationPolicy(policy: PolicyRecord, agentId: string): policy is PolicyRecord & { scope: PolicyScope } {
  const scope = policy.scope
  const keys = Object.keys(scope).filter((key) => scope[key] !== null && scope[key] !== undefined)
  return policy.agent_id === agentId && scope.tool_name === 'model' && scope.action === 'generate' && scope.environment_id === 'control_plane'
    && keys.every((key) => ['tool_name', 'action', 'environment_id', 'resource', 'parameters'].includes(key))
    && (scope.resource === undefined || scope.resource === null || (typeof scope.resource === 'string' && scope.resource.length > 0))
    && (scope.parameters === undefined || (record(scope.parameters) && (Object.keys(scope.parameters).length === 0 || (Object.keys(scope.parameters).length === 1 && typeof scope.parameters.model === 'string' && scope.parameters.model.length > 0))))
}
export function revocablePolicy(policy: PolicyRecord, agentId: string) { return policy.agent_id === agentId && policy.status === 'active' }
export function mergePolicyPage(previous: PolicyCollection | null, incoming: PolicyCollection): PolicyCollection {
  const policies = new Map((previous?.policies ?? []).map((policy) => [policy.id, policy]))
  for (const policy of incoming.policies) {
    const prior = policies.get(policy.id)
    if (!prior || policy.revision >= prior.revision) policies.set(policy.id, policy)
  }
  return { ...incoming, policies: [...policies.values()] }
}
export function policyRequestIdentity(makeId: () => string = () => crypto.randomUUID()) {
  let previous: { signature: string; id: string } | null = null
  return (payload: unknown) => {
    const signature = JSON.stringify(payload)
    if (!previous || previous.signature !== signature) previous = { signature, id: makeId() }
    return previous.id
  }
}
