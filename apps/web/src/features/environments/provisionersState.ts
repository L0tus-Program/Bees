import { ApiError } from '../../api/client'
import type { ProvisionerRecord } from '../../api/provisioners'

// A canonical revocation cannot disappear because an older read finished later.
export function checkedProvisionerSnapshot(items: ProvisionerRecord[], known: ReadonlyMap<string, ProvisionerRecord>) {
  for (const item of items) {
    const prior = known.get(item.provisioner_id)
    if (prior && (item.revision < prior.revision || prior.status === 'revoked' && item.status !== 'revoked'
      || item.revision === prior.revision && item.status !== prior.status)) throw new ApiError('state_conflict')
  }
  return items
}
