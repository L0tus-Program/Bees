import { ApiError, record, request } from './client'
import type { PairedHost } from './environments'

export interface ProvisionerRecord { provisioner_id: string; installation_id: string; host_id: string; revision: number; status: 'active' | 'revoked' }
export interface ProvisionerPage { items: ProvisionerRecord[]; has_more: boolean; next_offset: number | null }
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0
const base = (host: PairedHost) => `/environments/hosts/${encodeURIComponent(host.host_id)}/provisioners`
function checked(value: unknown, host: PairedHost): ProvisionerRecord {
  if (!record(value) || !uuid(value.provisioner_id) || !uuid(value.installation_id) || !uuid(value.host_id)
    || value.host_id !== host.host_id || value.installation_id !== host.installation_id || !positive(value.revision)
    || !['active', 'revoked'].includes(String(value.status))) throw new ApiError('invalid_response')
  // Never put transport additions, credentials or internal ledgers in UI state.
  return { provisioner_id: value.provisioner_id, installation_id: value.installation_id, host_id: value.host_id, revision: value.revision, status: value.status as ProvisionerRecord['status'] }
}
export async function getProvisioners(host: PairedHost, signal?: AbortSignal, offset = 0): Promise<ProvisionerPage> {
  if (!Number.isSafeInteger(offset) || offset < 0 || offset > 1_000_000 || !uuid(host.host_id) || !uuid(host.installation_id)) throw new ApiError('invalid_request')
  const value = await request(`${base(host)}?offset=${offset}&limit=100`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.provisioners) || value.provisioners.length > 100 || typeof value.has_more !== 'boolean'
    || (value.has_more ? value.provisioners.length === 0 || value.next_offset !== offset + value.provisioners.length : value.next_offset !== null)) throw new ApiError('invalid_response')
  const items = value.provisioners.map((item) => checked(item, host))
  if (new Set(items.map((item) => item.provisioner_id)).size !== items.length) throw new ApiError('invalid_response')
  return { items, has_more: value.has_more, next_offset: value.next_offset as number | null }
}
export async function revokeProvisioner(host: PairedHost, snapshot: ProvisionerRecord, requestId: string, signal?: AbortSignal): Promise<ProvisionerRecord> {
  if (snapshot.host_id !== host.host_id || snapshot.installation_id !== host.installation_id || !uuid(snapshot.provisioner_id)
    || !positive(snapshot.revision) || snapshot.status !== 'active' || !uuid(requestId)) throw new ApiError('invalid_request')
  const value = checked(await request(`${base(host)}/${encodeURIComponent(snapshot.provisioner_id)}/revoke`, {
    authenticated: true, signal, body: { expected_revision: snapshot.revision, client_request_id: requestId },
  }), host)
  if (value.provisioner_id !== snapshot.provisioner_id || value.status !== 'revoked' || value.revision <= snapshot.revision) throw new ApiError('invalid_response')
  return value
}
