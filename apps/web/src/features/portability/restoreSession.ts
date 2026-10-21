import { ApiError } from '../../api/client'
export const RESTORE_PENDING_KEY = 'bees.ui.restore.pending.v1'
export function pendingRestore(): string | null {
  const value = sessionStorage.getItem(RESTORE_PENDING_KEY)
  if (value !== null && !/^[a-z][a-z0-9-]{0,62}$/.test(value)) throw new ApiError('restore_state_unavailable')
  return value
}
export function rememberRestore(destination: string) {
  if (!/^[a-z][a-z0-9-]{0,62}$/.test(destination)) throw new ApiError('invalid_request')
  sessionStorage.setItem(RESTORE_PENDING_KEY, destination)
  if (pendingRestore() !== destination) throw new ApiError('restore_state_unavailable')
}
export function clearRestorePending() {
  sessionStorage.removeItem(RESTORE_PENDING_KEY)
  if (pendingRestore() !== null) throw new ApiError('restore_state_unavailable')
}
