import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { getSafety, sendSafetyCommand } from '../../api/safety'
import type { SafetyKind, SafetyState } from '../../api/safety'
import { ErrorNotice } from '../../components/Feedback'
import { FLIGHT, inFlightTotal, safetyFailure } from './state'
import type { SafetyIntent } from './state'

type Notice = 'stopped' | 'resumed' | 'conflict' | null
const POLL_MS = 5000
// Reserva órfã continua contada: o acompanhamento automático para depois de 5 minutos.
const POLL_LIMIT = 60

export function SafetyStatusView({ state, disabled, onStart }: { state: SafetyState; disabled: boolean; onStart: (kind: SafetyKind) => void }) {
  const { t, i18n } = useTranslation('safety')
  const stopped = state.status === 'stopped'
  const running = FLIGHT.filter((key) => state.in_flight[key] > 0)
  return <div className="safety-status">
    <div className="safety-summary">
      <h2 id="safety-heading"><span className={`safety-dot ${stopped ? 'stopped' : ''}`} aria-hidden="true" />{t(stopped ? 'stoppedTitle' : 'runningTitle')}</h2>
      <p>{t(stopped ? 'stoppedDescription' : 'runningDescription')}</p>
      {stopped && <p className="quiet-note">{t('stoppedSince', { time: new Date(state.changed_at).toLocaleString(i18n.resolvedLanguage) })}</p>}
      {stopped && state.reason && <p className="quiet-note">{t('reason', { reason: state.reason })}</p>}
    </div>
    <button type="button" className={`button ${stopped ? 'primary' : 'danger'}`} disabled={disabled} onClick={() => onStart(stopped ? 'resume' : 'stop')}>{t(stopped ? 'resume' : 'stop')}</button>
    {(stopped || running.length > 0) && <div className="safety-flight" aria-live="polite">
      <h3 className="section-label">{t('inFlightTitle')}</h3>
      {running.length === 0 ? <p className="quiet-note">{t('inFlightNone')}</p>
        : <ul>{running.map((key) => <li key={key}>{t(key)}: <strong>{state.in_flight[key]}</strong></li>)}</ul>}
    </div>}
  </div>
}

export function SafetyConfirmation({ intent, busy, lost, onReason, onConfirm, onCancel }: { intent: SafetyIntent; busy: boolean; lost: boolean; onReason: (reason: string) => void; onConfirm: () => void; onCancel: () => void }) {
  const { t } = useTranslation('safety')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const stop = intent.kind === 'stop'
  return <section className="tool-confirmation safety-confirmation" aria-labelledby="safety-confirmation-heading">
    <h3 id="safety-confirmation-heading" ref={heading} tabIndex={-1}>{t(stop ? 'stopConfirm' : 'resumeConfirm')}</h3>
    <p>{t(stop ? 'stopImpact' : 'resumeImpact')}</p>
    {/* Depois do envio o motivo fica fixo: o replay só é reconhecido com o pedido idêntico. */}
    {stop && <label className="field"><span>{t('reasonLabel')}</span><input type="text" maxLength={500} value={intent.reason} disabled={busy || intent.sent} onChange={(event) => onReason(event.target.value)} /></label>}
    {lost && <p className="form-error" role="alert">{t('lost')}</p>}
    <div className="tool-actions">
      <button type="button" className="text-button" disabled={busy} onClick={onCancel}>{t('cancel')}</button>
      <button type="button" className={`button ${stop ? 'danger' : 'primary'}`} disabled={busy} onClick={onConfirm}>{t(busy ? 'working' : lost ? 'retry' : stop ? 'confirmStop' : 'confirmResume')}</button>
    </div>
  </section>
}

export function SafetyControl() {
  const { t } = useTranslation('safety')
  const [state, setState] = useState<SafetyState | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [intent, setIntent] = useState<SafetyIntent | null>(null)
  const [busy, setBusy] = useState(false)
  const [lost, setLost] = useState(false)
  const [notice, setNotice] = useState<Notice>(null)
  const reader = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const intentRef = useRef(intent); intentRef.current = intent
  // Acompanhamento em segundo plano não marca a região como ocupada nem trava os botões.
  const load = useCallback(async (background = false) => {
    reader.current?.abort()
    const operation = new AbortController(); reader.current = operation
    if (!background) setLoading(true)
    try {
      const next = await getSafety(operation.signal)
      if (operation.signal.aborted) return
      setState(next); setError(null)
      // Decisão ainda não enviada sobre revisão antiga não vale para o estado novo.
      const pending = intentRef.current
      if (pending && !pending.sent && pending.revision !== next.revision) { setIntent(null); setNotice('conflict') }
    } catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure)) }
    finally { if (!operation.signal.aborted && !background) setLoading(false) }
  }, [])
  useEffect(() => { void load(); return () => { reader.current?.abort(); mutation.current?.abort() } }, [load])
  // Parado com operações já enviadas: acompanha o encerramento sem decidir nada.
  const [polls, setPolls] = useState(0)
  const watching = !!state && state.status === 'stopped' && inFlightTotal(state) > 0 && !intent && !busy && !loading && polls < POLL_LIMIT
  useEffect(() => {
    if (!watching) return
    const timer = window.setTimeout(() => { setPolls((count) => count + 1); void load(true) }, POLL_MS)
    return () => window.clearTimeout(timer)
  }, [watching, state, load])
  function start(kind: SafetyKind) {
    if (!state) return
    setNotice(null); setLost(false); setError(null)
    setIntent({ kind, revision: state.revision, requestId: crypto.randomUUID(), reason: '', sent: false })
  }
  function cancel() {
    const sent = intent?.sent
    setIntent(null); setLost(false)
    // Pedido enviado sem confirmação pode ter sido aplicado: relê o estado canônico.
    if (sent) void load()
  }
  async function confirm() {
    if (!intent || busy) return
    const command = { ...intent, sent: true }
    const reason = command.reason.trim()
    setIntent(command); setBusy(true); setError(null); setNotice(null)
    const operation = new AbortController(); mutation.current = operation
    try {
      const next = await sendSafetyCommand({
        client_request_id: command.requestId, kind: command.kind, expected_revision: command.revision,
        ...(command.kind === 'stop' && reason ? { reason } : {}),
      }, operation.signal)
      if (operation.signal.aborted) return
      setState(next); setIntent(null); setLost(false); setPolls(0); setNotice(command.kind === 'stop' ? 'stopped' : 'resumed')
    } catch (failure) {
      if (operation.signal.aborted) return
      const apiError = asApiError(failure)
      const kind = safetyFailure(apiError)
      if (kind === 'lost') setLost(true)
      else { setIntent(null); setLost(false); if (kind === 'conflict') { setNotice('conflict'); void load() } else setError(apiError) }
    } finally { if (!operation.signal.aborted) setBusy(false) }
  }
  const stopped = state?.status === 'stopped'
  return <section className={`surface safety-control ${stopped ? 'stopped' : ''}`} aria-labelledby={state ? 'safety-heading' : undefined} aria-label={state ? undefined : t('runningTitle')} aria-busy={loading || busy}>
    {!state && loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {state && <SafetyStatusView state={state} disabled={busy || !!intent} onStart={start} />}
    {notice && <p className={notice === 'conflict' ? 'form-error' : 'form-success'} role={notice === 'conflict' ? 'alert' : 'status'}>{t(notice)}</p>}
    {error && <ErrorNotice error={error} />}
    {intent && <SafetyConfirmation intent={intent} busy={busy} lost={lost} onReason={(reason) => setIntent({ ...intent, reason })} onConfirm={() => { void confirm() }} onCancel={cancel} />}
    {!intent && <button type="button" className="text-button" disabled={loading || busy} onClick={() => { setNotice(null); setPolls(0); void load() }}>{t('refresh')}</button>}
  </section>
}
