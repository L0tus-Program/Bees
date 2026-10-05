import { ApiError, record, request } from './client'
import { isAgent } from './onboarding'
import type { AgentSummary, ModelConfig } from './onboarding'

export interface BeeProfile {
  name: string; purpose: string; instructions: string; memory_enabled: boolean
}

export interface MemoryRecord {
  id: string; revision: number; scope: 'user' | 'agent' | 'task'; agent_id: string | null; task_id: string | null
  content: string; kind: 'fact' | 'preference'; source_ref: string | null; observed_at: string | null; created_at: string; updated_at: string
}

export interface MemoryCollection { memories: MemoryRecord[]; tasks: { id: string; title: string }[]; has_more: boolean }
export interface MemoryContent { content: string; kind: MemoryRecord['kind']; source_ref: string | null; observed_at: string | null }
export interface NewMemory extends MemoryContent { scope: MemoryRecord['scope']; task_id?: string }

const pathFor = (id: string) => `/agents/${encodeURIComponent(id)}`
const nullableString = (value: unknown) => value === null || typeof value === 'string'

export function isMemory(value: unknown): value is MemoryRecord {
  return record(value) && ['id', 'content', 'created_at', 'updated_at'].every((key) => typeof value[key] === 'string')
    && Number.isInteger(value.revision) && (value.revision as number) > 0 && ['user', 'agent', 'task'].includes(String(value.scope))
    && ['fact', 'preference'].includes(String(value.kind))
    && ['agent_id', 'task_id', 'source_ref', 'observed_at'].every((key) => nullableString(value[key]))
    && (value.scope !== 'user' || (value.agent_id === null && value.task_id === null))
    && (value.scope !== 'agent' || (typeof value.agent_id === 'string' && value.task_id === null))
    && (value.scope !== 'task' || (typeof value.agent_id === 'string' && typeof value.task_id === 'string'))
    && (value.kind !== 'fact' || (typeof value.source_ref === 'string' && value.source_ref.trim().length > 0
      && typeof value.observed_at === 'string' && !Number.isNaN(Date.parse(value.observed_at))))
}

export async function saveProfile(id: string, revision: number, profile: BeeProfile, signal?: AbortSignal): Promise<AgentSummary> {
  const value = await request(`${pathFor(id)}/profile`, { body: { expected_revision: revision, ...profile }, authenticated: true, signal })
  if (!isAgent(value) || value.id !== id) throw new ApiError('invalid_response')
  return value
}

export async function saveConfiguration(id: string, revision: number, config: ModelConfig, validationToken: string, apiKey: string, signal?: AbortSignal): Promise<AgentSummary> {
  const value = await request(`${pathFor(id)}/configuration`, {
    body: { expected_revision: revision, config, validation_token: validationToken, ...(apiKey ? { api_key: apiKey } : {}) }, authenticated: true, signal, timeoutMs: 15000,
  })
  if (!isAgent(value) || value.id !== id) throw new ApiError('invalid_response')
  return value
}

export async function getMemories(id: string, signal?: AbortSignal): Promise<MemoryCollection> {
  const value = await request(`${pathFor(id)}/memories`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.memories) || !value.memories.every(isMemory)
    || !Array.isArray(value.tasks) || !value.tasks.every((task) => record(task) && typeof task.id === 'string' && typeof task.title === 'string')
    || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  if (value.memories.some((memory) => memory.scope !== 'user' && memory.agent_id !== id)) throw new ApiError('invalid_response')
  return value as unknown as MemoryCollection
}

export async function createMemory(id: string, memory: NewMemory, signal?: AbortSignal): Promise<MemoryRecord> {
  const value = await request(`${pathFor(id)}/memories`, { body: memory, authenticated: true, signal })
  if (!isMemory(value) || (value.scope !== 'user' && value.agent_id !== id)) throw new ApiError('invalid_response')
  return value
}

export async function updateMemory(id: string, memoryId: string, revision: number, memory: MemoryContent, signal?: AbortSignal): Promise<MemoryRecord> {
  const value = await request(`${pathFor(id)}/memories/${encodeURIComponent(memoryId)}/update`, { body: { expected_revision: revision, ...memory }, authenticated: true, signal })
  if (!isMemory(value) || value.id !== memoryId || (value.scope !== 'user' && value.agent_id !== id)) throw new ApiError('invalid_response')
  return value
}

export async function deleteMemory(id: string, memoryId: string, revision: number, signal?: AbortSignal) {
  await request(`${pathFor(id)}/memories/${encodeURIComponent(memoryId)}/delete`, { body: { expected_revision: revision }, authenticated: true, signal })
}
