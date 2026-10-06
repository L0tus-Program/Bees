import { ApiError } from '../../api/client'
import { getPairedHosts } from '../../api/environments'
import type { PairedHost } from '../../api/environments'

export async function loadPairedHosts(signal?: AbortSignal) {
  const hosts: PairedHost[] = []; const ids = new Set<string>(); let offset = 0; let installation: string | null = null
  while (offset <= 1_000_000) {
    const page = await getPairedHosts(signal, offset)
    for (const host of page.items) {
      if (ids.has(host.host_id) || installation !== null && installation !== host.installation_id) throw new ApiError('state_conflict')
      installation = host.installation_id; ids.add(host.host_id); hosts.push(host)
    }
    if (page.next_offset === null) return hosts
    if (page.next_offset <= offset) throw new ApiError('invalid_response')
    offset = page.next_offset
  }
  throw new ApiError('invalid_response')
}
export function pairedHostStatus(host: PairedHost, now = Date.now()): 'pending' | 'active' | 'revoked' | 'expired' | 'offline' {
  if (host.status === 'pending') return host.confirmable && Date.parse(host.pairing_expires_at) > now ? 'pending' : 'expired'
  if (host.status === 'revoked') return 'revoked'
  const age = host.last_seen === null ? Infinity : now - Date.parse(host.last_seen)
  return host.online && age <= 60_000 && age >= -5_000 && host.diagnostic !== null ? 'active' : 'offline'
}
export function sameHostAuthority(before: PairedHost, after: PairedHost) {
  return before.host_id === after.host_id && before.installation_id === after.installation_id && before.revision === after.revision && before.status === after.status && before.fingerprint === after.fingerprint
}
