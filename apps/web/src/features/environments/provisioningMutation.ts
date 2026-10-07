import { asApiError } from '../../api/client'
import type { ProvisioningPlan, ProvisioningReviewState } from '../../api/provisioning'
import { uncertainEnvironmentMutation } from './state'

// Reconcile through a fresh read, never by repeating an uncertain human decision.
export async function reconcileProvisioningMutation(
  action: 'prepare' | 'authorize' | 'revoke',
  write: () => Promise<ProvisioningPlan>,
  read: () => Promise<ProvisioningReviewState>,
  signal: AbortSignal,
  onReconciling: (notice: 'uncertain' | 'conflict') => void,
) {
  let notice: string
  try {
    const result = await write()
    notice = result.status === 'outcome_unknown' ? 'unknown' : result.status === 'revoked' ? 'revoked' : action === 'prepare' ? 'prepared' : 'saved'
  } catch (failure) {
    signal.throwIfAborted()
    const issue = asApiError(failure)
    if (!uncertainEnvironmentMutation(issue) && issue.status !== 409) throw issue
    onReconciling(issue.status === 409 ? 'conflict' : 'uncertain')
    notice = issue.status === 409 ? 'conflict' : 'reconciled'
  }
  signal.throwIfAborted()
  const state = await read()
  signal.throwIfAborted()
  return { state, notice }
}
