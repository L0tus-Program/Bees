import { ApiError, record } from '../../api/client'
import { checkedExport } from './api'
import type { ExportReceipt } from './api'

export const EXPORT_STATE_KEY = 'bees.ui.export.v1'
export type ExportState = { status: 'empty' } | { status: 'ready'; receipt: ExportReceipt } | { status: 'pending'; previous: ExportReceipt | null } | { status: 'invalid' | 'unavailable' }
export function restoreExportState(): ExportState {
  try {
    const raw = sessionStorage.getItem(EXPORT_STATE_KEY)
    if (raw === null) return { status: 'empty' }
    const value: unknown = JSON.parse(raw)
    if (!record(value) || value.version !== 1 || Object.keys(value).length !== 3) return { status: 'invalid' }
    if (value.status === 'ready' && 'receipt' in value) return { status: 'ready', receipt: checkedExport(value.receipt) }
    if (value.status === 'pending' && 'previous' in value) return { status: 'pending', previous: value.previous === null ? null : checkedExport(value.previous) }
    return { status: 'invalid' }
  } catch (failure) { return { status: failure instanceof SyntaxError || failure instanceof ApiError ? 'invalid' : 'unavailable' } }
}
function save(value: unknown) {
  const raw = JSON.stringify(value)
  sessionStorage.setItem(EXPORT_STATE_KEY, raw)
  if (sessionStorage.getItem(EXPORT_STATE_KEY) !== raw) throw new ApiError('export_state_unavailable')
}
// Only reference metadata, never bytes, content, credentials or CSRF tokens.
export function rememberExportPending(previous: ExportReceipt | null) {
  save({ version: 1, status: 'pending', previous: previous === null ? null : checkedExport(previous) })
}
export function rememberExportReceipt(receipt: ExportReceipt) {
  save({ version: 1, status: 'ready', receipt: checkedExport(receipt) })
}
export function clearExportState() {
  sessionStorage.removeItem(EXPORT_STATE_KEY)
  if (sessionStorage.getItem(EXPORT_STATE_KEY) !== null) throw new ApiError('export_state_unavailable')
}
