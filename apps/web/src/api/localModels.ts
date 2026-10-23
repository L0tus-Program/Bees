import { ApiError, record, request } from './client'

export interface SuggestedLocalModel { id: string; name: string; download_bytes: number; min_memory_gb: number; tool_calls: boolean; source: string; note: string }
export interface InstalledLocalModel { name: string; size_bytes: number | null; modified_at: string | null }
export interface LocalModelPull {
  id: string; model: string; endpoint: string; status: 'running' | 'completed' | 'failed' | 'cancelled'; phase: string
  total_bytes: number; completed_bytes: number; error: string | null; started_at: string; finished_at: string | null
}
export interface LocalModelStatus { endpoint: string; reachable: boolean; version: string | null; models: InstalledLocalModel[]; suggested: SuggestedLocalModel[]; pulls: LocalModelPull[] }

const base = '/local-models'

function isPull(value: unknown): value is LocalModelPull {
  return record(value) && ['id', 'model', 'status', 'phase'].every((key) => typeof value[key] === 'string')
    && Number.isSafeInteger(value.total_bytes) && Number.isSafeInteger(value.completed_bytes)
}

export async function getLocalModels(endpoint: string | undefined, signal?: AbortSignal): Promise<LocalModelStatus> {
  const query = endpoint ? `?${new URLSearchParams({ endpoint }).toString()}` : ''
  const value = await request(`${base}${query}`, { authenticated: true, signal })
  if (!record(value) || typeof value.reachable !== 'boolean' || !Array.isArray(value.models) || !Array.isArray(value.suggested)
    || !Array.isArray(value.pulls) || !value.pulls.every(isPull)) throw new ApiError('invalid_response')
  return value as unknown as LocalModelStatus
}

export async function startLocalPull(endpoint: string | undefined, model: string, clientRequestId: string): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls`, { authenticated: true, body: { client_request_id: clientRequestId, model, confirm_download: true, ...(endpoint ? { endpoint } : {}) } })
  if (!isPull(value)) throw new ApiError('invalid_response')
  return value
}

export async function getLocalPull(id: string, signal?: AbortSignal): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls/${encodeURIComponent(id)}`, { authenticated: true, signal })
  if (!isPull(value)) throw new ApiError('invalid_response')
  return value
}

export async function cancelLocalPull(id: string): Promise<LocalModelPull> {
  const value = await request(`${base}/pulls/${encodeURIComponent(id)}/cancel`, { authenticated: true, body: {} })
  if (!isPull(value)) throw new ApiError('invalid_response')
  return value
}

export async function deleteLocalModel(endpoint: string | undefined, model: string) {
  await request(`${base}/delete`, { authenticated: true, body: { model, confirm_delete: true, ...(endpoint ? { endpoint } : {}) } })
}

export function formatBytes(value: number, locale: string) {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let unit = 0
  while (size >= 1000 && unit < units.length - 1) { size /= 1000; unit += 1 }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: unit > 2 ? 1 : 0 }).format(size)} ${units[unit]}`
}
