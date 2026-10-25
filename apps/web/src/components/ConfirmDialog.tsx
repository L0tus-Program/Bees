import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { registerConfirmHost } from './confirmAction'
import type { ConfirmRequest } from './confirmAction'


function ConfirmDialog({ request, onClose }: { request: ConfirmRequest; onClose: (value: boolean) => void }) {
  const { t } = useTranslation('dialog')
  const dialog = useRef<HTMLDialogElement>(null)
  const id = useId()
  useEffect(() => {
    const element = dialog.current
    if (!element) return
    // Modal nativo quando disponível: foco preso no diálogo e Esc cancela.
    if (typeof element.showModal === 'function' && !element.open) { try { element.showModal() } catch { element.setAttribute('open', '') } } else element.setAttribute('open', '')
  }, [])
  return <dialog ref={dialog} className="confirm-dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-message`} onCancel={(event) => { event.preventDefault(); onClose(false) }}>
    <h2 id={`${id}-title`}>{t('title')}</h2>
    <p id={`${id}-message`}>{request.message}</p>
    <div className="dialog-actions">
      <button type="button" className="button secondary" autoFocus onClick={() => onClose(false)}>{t('cancel')}</button>
      <button type="button" className="button danger" onClick={() => onClose(true)}>{request.confirmLabel ?? t('confirm')}</button>
    </div>
  </dialog>
}


/** Montado uma vez no App: atende confirmAction com um diálogo modal acessível. */
export function ConfirmHost() {
  const [queue, setQueue] = useState<ConfirmRequest[]>([])
  useEffect(() => {
    registerConfirmHost((request) => setQueue((current) => [...current, request]))
    return () => registerConfirmHost(null)
  }, [])
  const current = queue[0]
  if (!current) return null
  return <ConfirmDialog key={queue.length + current.message} request={current} onClose={(value) => { current.resolve(value); setQueue((items) => items.slice(1)) }} />
}
