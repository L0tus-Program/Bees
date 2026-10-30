import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { Routine } from '../../api/routines'
import { clearManualRoutine, executeManualRoutine, getManualRoutineReceipt, manualRoutineIntent, preserveManualRoutine, restoreManualRoutine } from '../../api/routineManual'
import type { ManualRoutineIntent, ManualRoutineReceipt } from '../../api/routineManual'
import { ErrorNotice } from '../../components/Feedback'
import { RoutineDescription } from './RoutineDescription'

export function ManualRoutineRun({ agentId, routine, available, onClose, onCreated, onLock }: { agentId: string; routine: Routine | null; available: boolean; onClose: () => void; onCreated: (taskId: string) => void; onLock: (locked: boolean) => void }) {
  const { t } = useTranslation('routines')
  const [restored] = useState(() => restoreManualRoutine(agentId))
  const [pending, setPending] = useState<ManualRoutineIntent | null>(restored.intent)
  const [corrupt, setCorrupt] = useState(restored.corrupt)
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState(false)
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const locked = useRef(false)
  const writing = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => { onLock(busy || pending !== null || corrupt) }, [busy, pending, corrupt, onLock])
  useEffect(() => { heading.current?.focus() }, [routine, pending, corrupt, retry])
  useEffect(() => () => { writing.current?.abort() }, [])
  function finish(receipt: ManualRoutineReceipt) {
    try { clearManualRoutine(agentId); setStorageUnavailable(false) } catch { setStorageUnavailable(true) }
    setPending(null); setRetry(false); setError(null)
    onCreated(receipt.occurrence.task_id!)
  }
  async function send(intent: ManualRoutineIntent) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError(null); setPending(intent); setRetry(false)
    const controller = new AbortController(); writing.current = controller
    try { preserveManualRoutine(intent) } catch { setStorageUnavailable(true) }
    try {
      const receipt = await executeManualRoutine(intent, controller.signal)
      if (!controller.signal.aborted) finish(receipt)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asApiError(failure); setError(problem)
      if (problem.status >= 400 && problem.status < 500 && problem.code !== 'invalid_response') {
        try { clearManualRoutine(agentId) } catch { setStorageUnavailable(true) }
        setPending(null)
        if (problem.status === 409) onClose()
      }
    } finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  async function verify() {
    if (!pending || locked.current) return
    locked.current = true; setBusy(true); setError(null); setRetry(false)
    const controller = new AbortController(); writing.current = controller
    try {
      const receipt = await getManualRoutineReceipt(pending, controller.signal)
      if (!controller.signal.aborted) finish(receipt)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asApiError(failure)
      if (problem.status === 404) setRetry(true)
      else setError(problem)
    } finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  if (!routine && !pending && !corrupt && !error) return null
  return <section className="routine-review" aria-labelledby="routine-manual-heading" aria-busy={busy}>
    <h3 id="routine-manual-heading" ref={heading} tabIndex={-1}>{t('manualTitle')}</h3>
    {error && <ErrorNotice error={error} />}
    {storageUnavailable && <p role="status">{t('storageUnavailable')}</p>}
    {corrupt ? <><p role="alert">{t('manualCorrupt')}</p><button type="button" className="button secondary" onClick={() => { try { clearManualRoutine(agentId); setCorrupt(false) } catch { setStorageUnavailable(true) } }}>{t('manualDiscardRecord')}</button></> : pending ? <>
      <p role="status">{t(retry ? 'manualNotFound' : 'manualUncertain')}</p>
      <button type="button" className="button secondary" disabled={busy} onClick={() => { void verify() }}>{t('manualVerify')}</button>
      {retry && <button type="button" className="button primary" disabled={busy} onClick={() => { void send(pending) }}>{t('manualRetry')}</button>}
    </> : routine?.definition ? <>
      <RoutineDescription definition={routine.definition} />
      <p>{t('manualEffect')}</p>{!available && <p>{t('unavailable')}</p>}
      <div className="routine-actions"><button type="button" className="button primary" disabled={busy || !available} onClick={() => { void send(manualRoutineIntent(routine)) }}>{t('manualConfirm')}</button><button type="button" className="button secondary" disabled={busy} onClick={onClose}>{t('back')}</button></div>
    </> : null}
  </section>
}
