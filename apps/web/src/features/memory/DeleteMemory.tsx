import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { deleteMemory } from '../../api/bee'
import type { MemoryRecord } from '../../api/bee'
import { ErrorNotice } from '../../components/Feedback'

export function DeleteMemory({ agentId, memory, onDeleted, onCancel, onReload }: { agentId: string; memory: MemoryRecord; onDeleted: () => void; onCancel: () => void; onReload: () => void }) {
  const { t } = useTranslation('bee')
  const { t: product } = useTranslation('product')
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    return () => { controller.current?.abort(); dialog?.close() }
  }, [])

  async function remove() {
    if (busy) return
    setBusy(true); setError(null); controller.current = new AbortController()
    try {
      await deleteMemory(agentId, memory.id, memory.revision, controller.current.signal)
      if (!controller.current.signal.aborted) onDeleted()
    } catch (failure) { if (!controller.current.signal.aborted) setError(asApiError(failure)) }
    finally { setBusy(false) }
  }

  return <dialog ref={dialogRef} className="confirm-dialog" aria-labelledby="delete-memory-title" aria-describedby="delete-memory-impact" onCancel={(event) => { event.preventDefault(); if (!busy) onCancel() }}>
    <h2 id="delete-memory-title">{t('deleteMemoryTitle')}</h2><p id="delete-memory-impact">{t(memory.scope === 'user' ? 'deleteUserImpact' : 'deleteMemoryImpact')}</p><blockquote>{memory.content}</blockquote>
    {error && <><ErrorNotice error={error} /><button className="text-button" type="button" onClick={onReload}>{t('reloadCurrent')}</button></>}
    <div className="dialog-actions"><button className="button secondary" type="button" onClick={onCancel} disabled={busy} autoFocus>{t('cancel')}</button><button className="button danger" type="button" disabled={busy || !!error} onClick={() => { void remove() }}>{busy ? product('working') : t('confirmDelete')}</button></div>
  </dialog>
}
