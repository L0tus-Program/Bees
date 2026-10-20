import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { createExport, downloadExport, exportFilename } from './api'
import { clearExportState, rememberExportPending, rememberExportReceipt, restoreExportState } from './session'
import type { ExportState } from './session'
import './portability.css'

export default function ExportPanel({ onClose, onBusy }: { onClose: () => void; onBusy: (busy: boolean) => void }) {
  const { t, i18n } = useTranslation('portability')
  const [state, setState] = useState<ExportState>(restoreExportState)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState<'create' | 'download' | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [storageWarning, setStorageWarning] = useState(false)
  const [sent, setSent] = useState(false)
  const [progress, setProgress] = useState(0)
  const locked = useRef(false), operation = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const releases = useRef(new Set<() => void>())
  const receipt = state.status === 'ready' ? state.receipt : state.status === 'pending' ? state.previous : null
  useEffect(() => { heading.current?.focus(); const urls = releases.current; return () => { operation.current?.abort(); urls.forEach(release => release()); urls.clear() } }, [])
  function start(kind: 'create' | 'download') {
    if (locked.current) return null
    locked.current = true; const controller = new AbortController(); operation.current = controller
    setBusy(kind); onBusy(true); setError(null); setSent(false); return controller
  }
  function finish(controller: AbortController) {
    if (!controller.signal.aborted) { locked.current = false; setBusy(null); onBusy(false) }
  }
  async function create() {
    if (!consent || state.status === 'invalid' || state.status === 'unavailable') return
    const controller = start('create'); if (!controller) return
    setConsent(false)
    try {
      try { rememberExportPending(receipt) } catch { throw new ApiError('export_state_unavailable') }
      setState({ status: 'pending', previous: receipt }); setStorageWarning(false)
      const confirmed = await createExport(controller.signal)
      if (controller.signal.aborted) return
      setState({ status: 'ready', receipt: confirmed })
      try { rememberExportReceipt(confirmed) } catch { setStorageWarning(true) }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const problem = asApiError(failure); setError(problem)
        if (problem.code === 'csrf_missing' || [401, 403, 422].includes(problem.status)) {
          setState(receipt ? { status: 'ready', receipt } : { status: 'empty' })
          try { if (receipt) rememberExportReceipt(receipt); else clearExportState() } catch { setStorageWarning(true) }
        }
      }
    } finally { finish(controller) }
  }
  async function download() {
    if (!receipt) return
    const controller = start('download'); if (!controller) return
    setProgress(0)
    try {
      const blob = await downloadExport(receipt, controller.signal, (received, total) => { if (!controller.signal.aborted) setProgress(Math.floor(received / total * 100)) })
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob), anchor = document.createElement('a')
      anchor.href = url; anchor.download = exportFilename(receipt)
      const release = () => { clearTimeout(timer); URL.revokeObjectURL(url); releases.current.delete(release) }
      const timer = setTimeout(release, 30000); releases.current.add(release)
      try { document.body.append(anchor); anchor.click() } catch (failure) { release(); throw failure } finally { anchor.remove() }
      setSent(true)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { finish(controller) }
  }
  function forget() {
    if (locked.current) return
    try { clearExportState(); setState({ status: 'empty' }); setError(null); setStorageWarning(false); setSent(false); setConsent(false) }
    catch { setError(new ApiError('export_state_unavailable')) }
  }
  const format = (value: number) => new Intl.NumberFormat(i18n.resolvedLanguage).format(value)
  const message = error?.code === 'export_state_unavailable' ? 'storageError' : error?.code === 'export_integrity_failed' || error?.code === 'invalid_response' ? 'integrityError' : error?.code === 'export_missing' ? 'missing' : busy === null && error ? 'requestError' : null
  return <section className="surface export-panel" aria-labelledby="export-heading" aria-busy={busy !== null}>
    <div className="form-heading"><h1 id="export-heading" tabIndex={-1} ref={heading}>{t('title')}</h1><button type="button" className="text-button" disabled={busy !== null} onClick={onClose}>{t('back')}</button></div>
    <p>{t('scope')}</p><p className="transfer-notice">{t('privacy')}</p><p className="quiet-note">{t('exclusions')}</p>
    {message && <p className="form-error" role="alert">{t(message)}</p>}
    {state.status === 'invalid' || state.status === 'unavailable' ? <div><p className="transfer-notice">{t('stateError')}</p><button type="button" className="text-button" onClick={() => { setState(restoreExportState()); setError(null) }}>{t('readLocal')}</button><button type="button" className="text-button" onClick={forget}>{t('forget')}</button></div> : <>
      {state.status === 'pending' && busy !== 'create' && <p className="transfer-notice" role="status">{t('uncertain')}</p>}
      {storageWarning && <p className="transfer-notice" role="status">{t('storageWarning')}</p>}
      {receipt && <section className="export-receipt" aria-labelledby="export-receipt-heading"><h2 id="export-receipt-heading">{t('confirmed')}</h2><dl><div><dt>{t('size')}</dt><dd>{t('bytes', { value: format(receipt.size_bytes) })}</dd></div><div><dt>{t('rows')}</dt><dd>{format(receipt.rows)}</dd></div><div><dt>{t('blobs')}</dt><dd>{format(receipt.blobs)}</dd></div></dl><details><summary>{t('details')}</summary><p>{t('identifier')}: <code>{receipt.id}</code></p><p>SHA-256: <code>{receipt.sha256}</code></p></details><button type="button" className="button primary" disabled={busy !== null} onClick={() => { void download() }}>{t(busy === 'download' ? 'downloading' : 'download', { percent: progress })}</button>{sent && <p className="form-success" role="status">{t('downloadSent')}</p>}<p className="quiet-note">{t('downloadScope')}</p></section>}
      {busy === 'create' && <p role="status">{t('creating')}</p>}
      <form onSubmit={event => { event.preventDefault(); void create() }}><fieldset disabled={busy !== null}><legend>{t(receipt || state.status === 'pending' ? 'newCopy' : 'createTitle')}</legend><label className="checkbox-field"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>{t('consent')}</span></label><p className="quiet-note">{t('newCopyHelp')}</p><button type="submit" className="button secondary" disabled={!consent}>{t('create')}</button></fieldset></form>
      {(receipt || state.status === 'pending') && <><button type="button" className="text-button" disabled={busy !== null} onClick={forget}>{t('forget')}</button><p className="quiet-note">{t('forgetHelp')}</p></>}
    </>}
    <p className="quiet-note">{t('restoreScope')}</p>
  </section>
}
