import { ApiError, record, request, setCsrfToken } from './client'

export interface AuthStatus {
  configured: boolean
  authenticated: boolean
  user?: { name: string }
  csrf_token?: string
}

export interface ModelConfig {
  kind: 'openai_compatible' | 'ollama'
  endpoint: string
  model: string
  secret_ref?: string | null
  capabilities: { text: boolean; tool_calls: boolean }
  timeout_seconds?: number
  deadline_seconds?: number
  max_response_bytes?: number
  max_request_bytes?: number
}

export interface AgentSummary {
  id: string
  name: string
  purpose: string
  instructions: string
  provider_config: ModelConfig | null
  conversation_id: string | null
  revision: number
  status: 'active' | 'paused' | 'archived'
  memory_enabled: boolean
}

export interface Onboarding {
  agents: AgentSummary[]
  vault: { available: boolean; backend: string; reason?: string | null }
  environments: { id: string; status: string }[]
  next_steps: string[]
  has_more?: boolean
}

export interface Diagnostic { status: 'ok'; code: string; capabilities: ModelConfig['capabilities']; validation_token: string }
export interface Message { id: string; role: string; content: string; created_at: string }
export interface Conversation { conversation_id: string; messages: Message[]; has_more?: boolean }

export function isModelConfig(value: unknown): value is ModelConfig {
  return record(value) && ['openai_compatible', 'ollama'].includes(String(value.kind))
    && typeof value.endpoint === 'string' && typeof value.model === 'string'
    && record(value.capabilities) && typeof value.capabilities.text === 'boolean'
    && typeof value.capabilities.tool_calls === 'boolean'
}

export function isAgent(value: unknown): value is AgentSummary {
  return record(value) && ['id', 'name', 'purpose', 'instructions'].every((key) => typeof value[key] === 'string')
    && (value.conversation_id === null || typeof value.conversation_id === 'string')
    && (value.provider_config === null || isModelConfig(value.provider_config)) && typeof value.revision === 'number'
    && ['active', 'paused', 'archived'].includes(String(value.status)) && typeof value.memory_enabled === 'boolean'
}

export async function authStatus(signal?: AbortSignal): Promise<AuthStatus> {
  const value = await request('/auth/status', { signal })
  if (!record(value) || typeof value.configured !== 'boolean' || typeof value.authenticated !== 'boolean'
    || (value.authenticated && (!record(value.user) || typeof value.user.name !== 'string' || typeof value.csrf_token !== 'string'))) {
    throw new ApiError('invalid_response')
  }
  setCsrfToken(value.authenticated ? value.csrf_token as string : undefined)
  return value as unknown as AuthStatus
}

export async function authenticate(body: { bootstrap_token: string; name: string; password: string } | { password: string }, signal?: AbortSignal) {
  await request('bootstrap_token' in body ? '/auth/setup' : '/auth/login', { body, signal })
}

export async function logout() {
  await request('/auth/logout', { body: {}, authenticated: true })
  setCsrfToken()
}

export async function onboarding(signal?: AbortSignal): Promise<Onboarding> {
  const value = await request('/onboarding', { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.agents) || !value.agents.every(isAgent)
    || !record(value.vault) || typeof value.vault.available !== 'boolean' || typeof value.vault.backend !== 'string'
    || !Array.isArray(value.environments) || !value.environments.every((item) => record(item) && typeof item.id === 'string' && typeof item.status === 'string')
    || !Array.isArray(value.next_steps)) throw new ApiError('invalid_response')
  return value as unknown as Onboarding
}

export async function testModel(config: ModelConfig, apiKey: string, signal?: AbortSignal, agentId?: string): Promise<Diagnostic> {
  const value = await request('/models/test', { body: { config, ...(apiKey ? { api_key: apiKey } : {}), ...(agentId ? { agent_id: agentId } : {}) }, authenticated: true, signal, timeoutMs: 40000 })
  if (!record(value) || value.status !== 'ok' || typeof value.code !== 'string' || !record(value.capabilities)
    || typeof value.capabilities.text !== 'boolean' || typeof value.capabilities.tool_calls !== 'boolean'
    || typeof value.validation_token !== 'string' || !value.validation_token) throw new ApiError('invalid_response')
  return value as unknown as Diagnostic
}

export async function createAgent(body: { name: string; purpose: string; instructions: string; config: ModelConfig; validation_token: string; api_key?: string }, signal?: AbortSignal): Promise<AgentSummary> {
  const value = await request('/agents', { body, authenticated: true, signal, timeoutMs: 15000 })
  if (!isAgent(value)) throw new ApiError('invalid_response')
  return value
}

export async function messages(id: string, conversationId: string, signal?: AbortSignal): Promise<Conversation> {
  const value = await request(`/agents/${encodeURIComponent(id)}/messages?conversation_id=${encodeURIComponent(conversationId)}`, { authenticated: true, signal })
  if (!record(value) || value.conversation_id !== conversationId || !Array.isArray(value.messages)
    || !value.messages.every((item) => record(item) && ['id', 'role', 'content', 'created_at'].every((key) => typeof item[key] === 'string'))) throw new ApiError('invalid_response')
  return value as unknown as Conversation
}

export async function sendChat(id: string, conversationId: string, content: string, signal?: AbortSignal) {
  await request(`/agents/${encodeURIComponent(id)}/chat`, {
    body: { conversation_id: conversationId, content }, signal, authenticated: true, timeoutMs: 75000,
  })
}

export function configurationSignature(config: ModelConfig, apiKey: string) {
  // Memory only. Used to invalidate the successful check on every configuration edit.
  return JSON.stringify({ config, apiKey })
}
