import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { getBudget, putBudget } from '../../api/budgets'
import type { BudgetStatus, BudgetSummary } from '../../api/budgets'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { useWindowLabel } from './windowLabel'

const WINDOWS = [3600, 21600, 86400, 604800, 2592000]

// Apresentação pura: nunca mostra restante sem limite ativo confirmado pelo serviço.
export function BudgetSummaryView({ summary }: { summary: BudgetSummary }) {
  const { t, i18n } = useTranslation('budgets')
  const label = useWindowLabel()
  const number = new Intl.NumberFormat(i18n.resolvedLanguage)
  const tokens = (value: number) => t('tokens', { value: number.format(value) })
  const limit = summary.limit
  const exhausted = limit?.status === 'active' && summary.remaining_tokens === 0
  return <>
    <dl className="budget-stats">
      <div><dt>{t('window')}</dt><dd>{t('windowValue', { value: label(summary.window_seconds) })}</dd></div>
      <div><dt>{t('counted')}</dt><dd>{tokens(summary.counted_tokens)}</dd></div>
      <div><dt>{t('reported')}</dt><dd>{tokens(summary.reported_tokens)}</dd></div>
      <div><dt>{t('uncertain')}</dt><dd>{number.format(summary.unknown_entries)}</dd></div>
      <div><dt>{t('open')}</dt><dd>{number.format(summary.open_reservations)}</dd></div>
      <div><dt>{t('entries')}</dt><dd>{number.format(summary.entries)}</dd></div>
      {limit?.status === 'active' && <div><dt>{t('limit')}</dt><dd>{tokens(limit.token_limit)}</dd></div>}
      {limit?.status === 'active' && summary.remaining_tokens !== null && <div><dt>{t('remaining')}</dt><dd>{tokens(summary.remaining_tokens)}</dd></div>}
    </dl>
    <p className="quiet-note">{t('countedHelp')}</p>
    {summary.unknown_entries > 0 && <p className="transfer-notice">{t('uncertainHelp')}</p>}
    {!limit && <p className="quiet-note">{t('noLimit')}</p>}
    {limit?.status === 'disabled' && <p className="quiet-note">{t('disabledLimit')}</p>}
    {exhausted && <p className="form-error" role="status">{t('exhausted')}</p>}
  </>
}

export function BudgetPanel({ agent }: { agent: AgentSummary }) {
  const { t } = useTranslation('budgets')
  const label = useWindowLabel()
  const [data, setData] = useState<BudgetSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<'saved' | 'conflict' | 'limitTooSmall' | null>(null)
  const [reload, setReload] = useState(0)
  const [form, setForm] = useState({ token_limit: '', window_seconds: 86400, output_allowance: '4096', status: 'active' as BudgetStatus })
  const read = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)
  useEffect(() => {
    const controller = new AbortController(); read.current = controller
    getBudget(agent.id, controller.signal).then((summary) => {
      if (controller.signal.aborted) return
      // Erro posterior conserva o último estado carregado; nunca inventa orçamento livre.
      setData(summary); setError(null); setLoading(false)
    }, (failure: unknown) => { if (!controller.signal.aborted) { setError(asApiError(failure)); setLoading(false) } })
    return () => controller.abort()
  }, [agent.id, reload])
  useEffect(() => () => mutation.current?.abort(), [])
  const refresh = () => { read.current?.abort(); setLoading(true); setReload((value) => value + 1) }
  function edit(summary: BudgetSummary) {
    const limit = summary.limit
    setForm({
      token_limit: limit ? String(limit.token_limit) : '',
      window_seconds: limit?.window_seconds ?? summary.window_seconds,
      output_allowance: String(limit?.output_allowance ?? 4096),
      status: limit?.status ?? 'active',
    })
    setEditing(true); setNotice(null)
  }
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!data) return
    const tokenLimit = Number(form.token_limit), allowance = Number(form.output_allowance)
    if (!Number.isSafeInteger(tokenLimit) || !Number.isSafeInteger(allowance) || allowance < 1 || tokenLimit <= allowance) { setNotice('limitTooSmall'); return }
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setError(null); setNotice(null)
    try {
      const summary = await putBudget(agent.id, {
        token_limit: tokenLimit, window_seconds: form.window_seconds, output_allowance: allowance, status: form.status,
        ...(data.limit ? { expected_revision: data.limit.revision } : {}),
      }, controller.signal)
      if (!controller.signal.aborted) { setData(summary); setEditing(false); setNotice('saved') }
    } catch (failure) {
      if (controller.signal.aborted) return
      const apiError = asApiError(failure)
      if (apiError.status === 409) { setEditing(false); setNotice('conflict'); refresh() } else setError(apiError)
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  const windows = data?.limit && !WINDOWS.includes(data.limit.window_seconds) ? [...WINDOWS, data.limit.window_seconds] : WINDOWS
  return <section className="surface budget-panel" aria-labelledby="budget-heading" aria-busy={loading || busy}>
    <div className="form-heading"><div><h2 id="budget-heading">{t('title')}</h2></div>
      {data && !editing && <button className="button primary" type="button" disabled={loading || busy || !!error} onClick={() => edit(data)}>{t(data.limit ? 'editLimit' : 'setLimit')}</button>}</div>
    <p className="form-introduction">{t('description')}</p>
    <p className="transfer-notice">{t('notBilling')}</p>
    {notice && <p className={notice === 'saved' ? 'form-success' : 'form-error'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {error && <><ErrorNotice error={error} />{data && <p className="quiet-note">{t('stale')}</p>}</>}
    {data && <BudgetSummaryView summary={data} />}
    {editing && data && <form className="budget-form" onSubmit={(event) => { void save(event) }}>
      <label className="field"><span>{t('tokenLimit')}</span><input type="number" inputMode="numeric" min={2} step={1} required value={form.token_limit} disabled={busy} onChange={(event) => setForm({ ...form, token_limit: event.target.value })} /></label>
      <label className="field"><span>{t('windowLength')}</span><select value={form.window_seconds} disabled={busy} onChange={(event) => setForm({ ...form, window_seconds: Number(event.target.value) })}>{windows.map((seconds) => <option key={seconds} value={seconds}>{label(seconds)}</option>)}</select></label>
      <label className="field"><span>{t('outputAllowance')}</span><input type="number" inputMode="numeric" min={1} step={1} required value={form.output_allowance} disabled={busy} onChange={(event) => setForm({ ...form, output_allowance: event.target.value })} /></label>
      <p className="quiet-note">{t('outputHelp')}</p>
      <label className="budget-check"><input type="checkbox" checked={form.status === 'active'} disabled={busy} onChange={(event) => setForm({ ...form, status: event.target.checked ? 'active' : 'disabled' })} />{t('active')}</label>
      <div className="policy-actions"><button className="button secondary" type="button" disabled={busy} onClick={() => setEditing(false)}>{t('cancel')}</button><button className="button primary" type="submit" disabled={busy}>{t(busy ? 'saving' : 'save')}</button></div>
    </form>}
    {!editing && <button className="text-button" type="button" disabled={loading || busy} onClick={refresh}>{t('refresh')}</button>}
  </section>
}
