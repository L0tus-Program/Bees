import { ApiError, record, request } from '../../api/client'
import { MAX_EXPORT_BYTES } from './api'

export interface RestoreDestination { id: string; available: boolean; state: 'inactive_only' }
export const restoreReviews = ['new_identity', 'provider_reconnection', 'host_reconnection', 'policy_and_grant_review', 'routine_review', 'task_review', 'unknown_outcomes', 'explicit_global_resume', 'provisioner_state_not_restored'] as const
export interface RestoreReport {
  restore_id: string; source_id: string; destination: string; status: 'inactive'; created_at: string; package_sha256: string
  source_schema: number; destination_schema: number; records: Record<string, number>; blobs: number
  changes: unknown[]; review_required: (typeof restoreReviews)[number][]; source_evidence: 'source.zip'; revision_policy: 'new-installation-baseline-original-revisions'
}
const alias = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9-]{0,62}$/.test(value)
const uuid = (value: unknown) => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value) && value !== '00000000-0000-0000-0000-000000000000'
const integer = (value: unknown, min = 0): value is number => Number.isSafeInteger(value) && Number(value) >= min
function invalid(): never { throw new ApiError('invalid_response') }
export function checkedDestinations(value: unknown): RestoreDestination[] {
  if (!Array.isArray(value) || value.length > 10) return invalid()
  const seen = new Set<string>()
  return value.map(item => {
    if (!record(item) || Object.keys(item).length !== 3 || !alias(item.id) || typeof item.available !== 'boolean' || item.state !== 'inactive_only' || seen.has(item.id)) return invalid()
    seen.add(item.id); return item as unknown as RestoreDestination
  })
}
export function checkedReport(value: unknown, destination: string): RestoreReport {
  const keys = ['restore_id', 'source_id', 'destination', 'status', 'created_at', 'package_sha256', 'source_schema', 'destination_schema', 'records', 'blobs', 'changes', 'review_required', 'source_evidence', 'revision_policy']
  if (!record(value) || Object.keys(value).length !== keys.length || !keys.every(key => key in value)
    || !uuid(value.restore_id) || !uuid(value.source_id) || !alias(value.destination) || value.destination !== destination || value.status !== 'inactive'
    || typeof value.created_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value.created_at) || !Number.isFinite(Date.parse(value.created_at))
    || typeof value.package_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(value.package_sha256) || !integer(value.source_schema, 17) || !integer(value.destination_schema, 17)
    || !record(value.records) || Object.keys(value.records).length === 0 || Object.keys(value.records).length > 100 || !Object.entries(value.records).every(([key, count]) => /^[a-z][a-z0-9_]{0,63}$/.test(key) && integer(count))
    || !integer(value.blobs) || !Array.isArray(value.changes) || !value.changes.every(item => record(item) && Object.keys(item).length === 5 && typeof item.table === 'string' && (item.entity_id === null || uuid(item.entity_id)) && typeof item.reason === 'string' && record(item.source) && record(item.destination))
    || !Array.isArray(value.review_required) || value.review_required.length !== restoreReviews.length || new Set(value.review_required).size !== value.review_required.length || !value.review_required.every(item => restoreReviews.includes(item))
    || value.source_evidence !== 'source.zip' || value.revision_policy !== 'new-installation-baseline-original-revisions') return invalid()
  return value as unknown as RestoreReport
}
function path(destination: string) {
  if (!alias(destination)) throw new ApiError('invalid_request')
  return `/restoration/destinations/${destination}`
}
export async function restoreDestinations(signal?: AbortSignal) { return checkedDestinations(await request('/restoration/destinations', { authenticated: true, signal })) }
export async function restoreReport(destination: string, signal?: AbortSignal) { return checkedReport(await request(`${path(destination)}/report`, { authenticated: true, signal }), destination) }
export async function uploadRestore(destination: string, file: Blob, consent: boolean, signal?: AbortSignal) {
  const target = path(destination)
  if (!consent || file.size === 0 || file.size > MAX_EXPORT_BYTES) throw new ApiError('invalid_request')
  return checkedReport(await request(`${target}/restore`, { restoreBody: file, authenticated: true, signal, timeoutMs: 120000 }), destination)
}
