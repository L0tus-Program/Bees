import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import { MAX_EXPORT_BYTES } from './api'
import { restoreDestinations, restoreReport, uploadRestore } from './restoration'
import type { RestoreDestination, RestoreReport } from './restoration'
import { clearRestorePending, pendingRestore, rememberRestore } from './restoreSession'

export default function RestorePanel({ onClose, onBusy }: { onClose: () => void; onBusy: (busy: boolean) => void }) {
  const { t, i18n } = useTranslation('restoration')
  const [destinations, setDestinations] = useState<RestoreDestination[]>([])
  const [destination, setDestination] = useState('')
  const [pending, setPending] = useState<string | null>(null)
  const [storageError, setStorageError] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState<'list' | 'report' | 'upload' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<RestoreReport | null>(null)
  const [checked, setChecked] = useState(false)
  const operation = useRef<AbortController | null>(null), locked = useRef(false)
  const heading = useRef<HTMLHeadingElement>(null), fileInput = useRef<HTMLInputElement>(null)
  useEffect(() => {
    heading.current?.focus()
    try { const saved = pendingRestore(); setPending(saved); if (saved) setDestination(saved) } catch { setStorageError(true) }
    return () => { operation.current?.abort() }
  }, [])
  function start(kind: 'list' | 'report' | 'upload') {
    if (locked.current) return null
    locked.current = true; const controller = new AbortController(); operation.current = controller
    setBusy(kind); onBusy(true); setError(null); return controller
  }
  function finish(controller: AbortController) {
    if (!controller.signal.aborted) { locked.current = false; setBusy(null); onBusy(false) }
  }
  async function list() {
    const controller = start('list'); if (!controller) return
    setChecked(false); setConsent(false)
    try {
      const items = await restoreDestinations(controller.signal)
      if (!controller.signal.aborted) { setDestinations(items); setLoaded(true) }
    } catch (failure) {
      if (!controller.signal.aborted) { setLoaded(false); setError(asApiError(failure).code === 'restoration_not_configured' ? 'notConfigured' : 'failure') }
    } finally { finish(controller) }
  }
  async function consult() {
    if (!destination) return
    const controller = start('report'); if (!controller) return
    setChecked(false); setConsent(false); setReport(null)
    try {
      const confirmed = await restoreReport(destination, controller.signal)
      if (controller.signal.aborted) return
      setReport(confirmed); setDestinations(items => items.map(item => item.id === destination ? { ...item, available: false } : item))
      try { clearRestorePending(); setPending(null) } catch { setStorageError(true) }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const problem = asApiError(failure)
        if (problem.code === 'restoration_not_found' && problem.status === 404) { setChecked(true); setError('noReport') }
        else setError('failure')
      }
    } finally { finish(controller) }
  }
  async function upload() {
    if (!file || !consent || !checked || pending || storageError || report || !destinations.find(item => item.id === destination)?.available) return
    if (file.size === 0 || file.size > MAX_EXPORT_BYTES) { setError('invalidFile'); return }
    const controller = start('upload'); if (!controller) return
    setConsent(false); setChecked(false)
    try {
      try { rememberRestore(destination) } catch { setStorageError(true); return }
      setPending(destination)
      const confirmed = await uploadRestore(destination, file, true, controller.signal)
      if (controller.signal.aborted) return
      setReport(confirmed); setDestinations(items => items.map(item => item.id === destination ? { ...item, available: false } : item)); setFile(null); if (fileInput.current) fileInput.current.value = ''
      try { clearRestorePending(); setPending(null) } catch { setStorageError(true) }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const problem = asApiError(failure)
        // These refusals happen before publication. Unknown failures retain the marker.
        const refused = problem.code === 'csrf_missing' ||
          ([400, 408, 413, 422].includes(problem.status) && ['restoration_invalid_length', 'restoration_upload_timeout', 'restoration_limit_exceeded', 'restoration_consent_required'].includes(problem.code)) ||
          (problem.status === 409 && ['restoration_busy', 'portability_invalid_package', 'portability_integrity_failed', 'portability_invalid_graph', 'portability_limit_exceeded', 'portability_sensitive_content', 'portability_unsupported_format', 'portability_unsupported_schema', 'portability_blob_missing', 'portability_blob_integrity_failed'].includes(problem.code))
        if (refused) {
          try { clearRestorePending(); setPending(null) } catch { setStorageError(true) }
          setError('failure')
        } else setError('uncertain')
      }
    }
    finally { finish(controller) }
  }
  const available = destinations.find(item => item.id === destination)?.available === true
  const format = (number: number) => new Intl.NumberFormat(i18n.resolvedLanguage).format(number)
  return <section className="surface export-panel" aria-labelledby="restore-heading" aria-busy={busy !== null}>
    <div className="form-heading"><h1 id="restore-heading" tabIndex={-1} ref={heading}>{t('title')}</h1><button type="button" className="text-button" disabled={busy !== null} onClick={onClose}>{t('back')}</button></div>
    <p>{t('scope')}</p><p className="transfer-notice">{t('privacy')}</p>
    {pending && <p className="transfer-notice" role="status">{t('pending', { destination: pending })}</p>}
    {storageError && <p className="form-error" role="alert">{t('storage')}</p>}
    {error && <p className={error === 'noReport' ? 'quiet-note' : 'form-error'} role="status">{t(error)}</p>}
    <p className="quiet-note">{t('lookupHelp')}</p>
    <button type="button" className="button secondary" disabled={busy !== null} onClick={() => { void list() }}>{t(busy === 'list' ? 'checking' : 'refresh')}</button>
    {loaded && destinations.length === 0 && <p>{t('notConfigured')}</p>}
    <label className="field"><span>{t('destination')}</span><select value={destination} disabled={busy !== null || pending !== null} onChange={event => { setDestination(event.target.value); setReport(null); setChecked(false); setConsent(false); setError(null); setFile(null); if (fileInput.current) fileInput.current.value = '' }}>
      <option value="">{t('choose')}</option>{pending && !destinations.some(item => item.id === pending) && <option value={pending}>{pending}</option>}{destinations.map(item => <option key={item.id} value={item.id}>{item.id} — {t(item.available ? 'available' : 'occupied')}</option>)}
    </select></label>
    <button type="button" className="text-button" disabled={!destination || busy !== null} onClick={() => { void consult() }}>{t(busy === 'report' ? 'checking' : 'consult')}</button>
    {report && <section className="export-receipt" aria-labelledby="restore-report-heading"><h2 id="restore-report-heading">{t('confirmed')}</h2><p>{t('confirmedHelp')}</p><dl><div><dt>{t('destination')}</dt><dd>{report.destination}</dd></div><div><dt>{t('rows')}</dt><dd>{format(Object.values(report.records).reduce((total, count) => total + count, 0))}</dd></div><div><dt>{t('blobs')}</dt><dd>{format(report.blobs)}</dd></div><div><dt>{t('changes')}</dt><dd>{format(report.changes.length)}</dd></div></dl><h3>{t('review')}</h3><ul>{report.review_required.map(item => <li key={item}>{t(item)}</li>)}</ul><details><summary>{t('details')}</summary><p><code>{report.restore_id}</code></p><p><code>{report.source_id}</code></p><p>SHA-256: <code>{report.package_sha256}</code></p><p><code>{report.source_evidence}</code></p></details></section>}
    {!report && <form onSubmit={event => { event.preventDefault(); void upload() }}><fieldset disabled={busy !== null || !available || !checked || pending !== null || storageError}><legend>{t('open')}</legend><label className="field"><span>{t('file')}</span><input type="file" accept=".zip,application/zip" ref={fileInput} onChange={event => { const selected = event.target.files?.[0] ?? null; setConsent(false); if (selected && (selected.size === 0 || selected.size > MAX_EXPORT_BYTES)) { setFile(null); event.target.value = ''; setError('invalidFile') } else { setFile(selected); setError(null) } }} /></label><label className="checkbox-field"><input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} /><span>{t('consent')}</span></label><button type="submit" className="button primary" disabled={!consent || !file}>{t(busy === 'upload' ? 'sending' : 'send')}</button></fieldset></form>}
  </section>
}
