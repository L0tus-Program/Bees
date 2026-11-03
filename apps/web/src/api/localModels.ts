import { ApiError, record, request } from './client'

export interface SuggestedLocalModel { id: string; name: string; download_bytes: number; min_memory_gb: number; tool_calls: boolean; source: string; note: string }
export interface InstalledLocalModel { name: string; size_bytes: number | null; modified_at: string | null }
export interface LocalModelPull {
  id: string; model: string; endpoint: string; status: 'running' | 'completed' | 'failed' | 'cancelled'; phase: string
  total_bytes: number; completed_bytes: number; error: string | null; started_at: string; finished_at: string | null
}
export interface LocalModelStatus { endpoint: string; reachable: boolean; version: string | null; models: InstalledLocalModel[]; suggested: SuggestedLocalModel[]; pulls: LocalModelPull[] }

const base = '/local-models'
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
const nullableText = (value: unknown) => value === null || text(value)
const canonicalModel = (value: string) => value.split('/').pop()?.includes(':') ? value : `${value}:latest`
function sameEndpoint(actual: string, requested?: string) {
  try {
    return new URL(actual).href.replace(/\/+$/, '') === new URL(requested || 'http://127.0.0.1:11434').href.replace(/\/+$/, '')
  } catch { return false }
}

function isModel(value: unknown): value is InstalledLocalModel {
  return record(value) && text(value.name) && (value.size_bytes === null || count(value.size_bytes)) && nullableText(value.modified_at)
}

function isSuggestion(value: unknown): value is SuggestedLocalModel {
  return record(value) && ['id', 'name', 'source', 'note'].every((key) => text(value[key]))
    && count(value.download_bytes) && count(value.min_memory_gb) && value.min_memory_gb > 0 && typeof value.tool_calls === 'boolean'
}

function isPull(value: unknown): value is LocalModelPull {
  return record(value) && ['id', 'model', 'endpoint', 'started_at'].every((key) => text(value[key]))
    && typeof value.status === 'string' && ['running', 'completed', 'failed', 'cancelled'].includes(value.status)
    && typeof value.phase === 'string' && ['starting', 'manifest', 'downloading', 'verifying', 'writing', 'success'].includes(value.phase)
    && count(value.total_bytes) && count(value.completed_bytes) && value.completed_bytes <= value.total_bytes
    && nullableText(value.error) && nullableText(value.finished_at)
}

export async function getLocalModels(endpoint: string | undefined, signal?: AbortSignal): Promise<LocalModelStatus> {
  const query = endpoint ? `?${new URLSearchParams({ endpoint }).toString()}` : ''
  const value = await request(`${base}${query}`, { authenticated: true, signal })
  if (!record(value) || !text(value.endpoint) || !nullableText(value.version) || typeof value.reachable !== 'boolean'
    || !Array.isArray(value.models) || !value.models.every(isModel) || !Array.isArray(value.suggested) || !value.suggested.every(isSuggestion)
    || !Array.isArray(value.pulls) || !value.pulls.every(isPull)
    || !sameEndpoint(value.endpoint, endpoint) || !value.pulls.every((pull) => sameEndpoint(pull.endpoint, value.endpoint as string))) throw new ApiError('invalid_response')
  return value as unknown as LocalModelStatus
}

export async function startLocalPull(endpoint: string | undefined, model: string, clientRequestId: string, signal?: AbortSignal): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls`, { authenticated: true, signal, body: { client_request_id: clientRequestId, model, confirm_download: true, ...(endpoint ? { endpoint } : {}) } })
  if (!isPull(value) || canonicalModel(value.model) !== canonicalModel(model) || !sameEndpoint(value.endpoint, endpoint)) throw new ApiError('invalid_response')
  return value
}

export async function getLocalPull(id: string, signal?: AbortSignal): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls/${encodeURIComponent(id)}`, { authenticated: true, signal })
  if (!isPull(value) || value.id !== id) throw new ApiError('invalid_response')
  return value
}

export async function cancelLocalPull(id: string, signal?: AbortSignal): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls/${encodeURIComponent(id)}/cancel`, { authenticated: true, signal, body: {} })
  if (!isPull(value) || value.id !== id) throw new ApiError('invalid_response')
  return value
}

export async function deleteLocalModel(endpoint: string | undefined, model: string, signal?: AbortSignal) {
  const value = await request(`${base}/delete`, { authenticated: true, signal, body: { model, confirm_delete: true, ...(endpoint ? { endpoint } : {}) } })
  if (!record(value) || !text(value.deleted) || canonicalModel(value.deleted) !== canonicalModel(model)) throw new ApiError('invalid_response')
}

export function formatBytes(value: number, locale: string) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1000 && unit < units.length - 1) { size /= 1000; unit += 1 }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: unit > 2 ? 1 : 0 }).format(size)} ${units[unit]}`
}
