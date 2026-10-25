import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createHook, getHook, revokeHook } from '../../api/hooks'
import type { RoutineHook as Hook } from '../../api/hooks'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

export function RoutineHook({ agentId, routineId }: { agentId: string; routineId: string }) {
  const { t, i18n } = useTranslation('hooks')
  const [open, setOpen] = useState(false)
  const [hook, setHook] = useState<Hook | null | undefined>(undefined)
  const [url, setUrl] = useState<string | null>(null)
  const [minInterval, setMinInterval] = useState(60)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  useEffect(() => {
    if (!open || hook !== undefined) return
    const controller = new AbortController()
    getHook(agentId, routineId, controller.signal).then(setHook, (failure) => { if (!controller.signal.aborted) setError(asApiError(failure)) })
    return () => controller.abort()
  }, [open, hook, agentId, routineId])
  async function act(action: () => Promise<void>) {
    setBusy(true); setError(null)
    try { await action() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  const active = hook?.status === 'active' ? hook : null
  return <details className="routine-hook" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
    <summary>{t('title')}</summary>
    <p className="quiet-note">{t('help')}</p>
    {error && <ErrorNotice error={error} />}
    {active ? <p>{t('active', { count: active.trigger_count })}{active.last_triggered_at && <> · {t('last', { time: new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(active.last_triggered_at)), result: active.last_result ?? '…' })}</>}</p> : hook === null && <p>{t('none')}</p>}
    {url && <div className="hook-url" role="status"><p><strong>{t('urlOnce')}</strong></p><code>{url}</code><button type="button" className="button secondary" onClick={() => { void navigator.clipboard?.writeText(url).then(() => setCopied(true)) }}>{t('copy')}</button>{copied && <small>{t('copied')}</small>}</div>}
    <div className="routine-actions">
      <label className="inline-select"><span>{t('interval')}</span><input type="number" min={10} max={86400} value={minInterval} onChange={(event) => setMinInterval(Number(event.target.value))} disabled={busy} /></label>
      <button type="button" className="button secondary" disabled={busy || hook === undefined || minInterval < 10 || minInterval > 86400} onClick={() => { void (active ? confirmAction(t('rotateConfirm')) : Promise.resolve(true)).then((ok) => { if (ok) void act(async () => { const result = await createHook(agentId, routineId, minInterval); setHook(result.hook); setUrl(result.url); setCopied(false) }) }) }}>{t(active ? 'rotate' : 'create')}</button>
      {active && <button type="button" className="text-button" disabled={busy} onClick={() => { void confirmAction(t('revokeConfirm')).then((ok) => { if (ok) void act(async () => { await revokeHook(agentId, active); setHook(null); setUrl(null) }) }) }}>{t('revoke')}</button>}
    </div>
  </details>
}
