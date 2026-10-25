import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { cancelLocalPull, deleteLocalModel, formatBytes, getLocalModels, getLocalPull, startLocalPull } from '../../api/localModels'
import type { LocalModelPull, LocalModelStatus, SuggestedLocalModel } from '../../api/localModels'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

type State = { state: 'loading' } | { state: 'ready'; data: LocalModelStatus } | { state: 'error'; error: ApiError }
const MODEL = /^[a-z0-9][a-z0-9._-]{0,99}(\/[a-z0-9][a-z0-9._-]{0,99})?(:[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/
const canonical = (value: string) => value.split('/').pop()?.includes(':') ? value : `${value}:latest`

export function LocalModelPanel({ endpoint, model, onSelect }: { endpoint: string; model: string; onSelect: (model: string) => void }) {
  const { t, i18n } = useTranslation('localModel')
  const [status, setStatus] = useState<State>({ state: 'loading' })
  const [pull, setPull] = useState<LocalModelPull | null>(null)
  const [other, setOther] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const target = endpoint || undefined
  const size = (value: number) => formatBytes(value, i18n.language)

  const load = useCallback(async (signal?: AbortSignal) => {
    setStatus({ state: 'loading' })
    try {
      const data = await getLocalModels(target, signal)
      setStatus({ state: 'ready', data })
      setPull((current) => current ?? data.pulls.find((item) => item.status === 'running') ?? null)
    } catch (error) { if (!signal?.aborted) setStatus({ state: 'error', error: asApiError(error) }) }
  }, [target])

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (!pull || pull.status !== 'running') return
    timer.current = setTimeout(() => {
      getLocalPull(pull.id).then((next) => {
        setPull(next)
        if (next.status === 'completed') { setNotice(t('completed', { model: next.model })); void load() }
      }).catch((error) => setFailure(asApiError(error)))
    }, 1000)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [pull, load, t])

  async function download(name: string, suggestion?: SuggestedLocalModel) {
    const message = suggestion
      ? t('confirmKnown', { model: name, size: size(suggestion.download_bytes), source: suggestion.source, endpoint: status.state === 'ready' ? status.data.endpoint : endpoint, memory: suggestion.min_memory_gb })
      : t('confirmUnknown', { model: name, endpoint: status.state === 'ready' ? status.data.endpoint : endpoint })
    if (!await confirmAction(message)) return
    setNotice(null); setFailure(null)
    try { setPull(await startLocalPull(target, name, crypto.randomUUID())) } catch (error) { setFailure(asApiError(error)) }
  }

  async function cancel() {
    if (!pull) return
    try { setPull(await cancelLocalPull(pull.id)); setNotice(t('cancelled')) } catch (error) { setFailure(asApiError(error)) }
  }

  async function remove(name: string) {
    if (!await confirmAction(t('removeConfirm', { model: name }))) return
    setNotice(null); setFailure(null)
    try { await deleteLocalModel(target, name); setNotice(t('removed')); await load() } catch (error) { setFailure(asApiError(error)) }
  }

  const data = status.state === 'ready' ? status.data : null
  const installed = new Set(data?.models.map((item) => canonical(item.name)) ?? [])
  const running = pull?.status === 'running'
  const percent = pull && pull.total_bytes ? Math.floor((pull.completed_bytes / pull.total_bytes) * 100) : null
  return (
    <section className="local-model" aria-labelledby="local-model-title">
      <h3 id="local-model-title">{t('title')}</h3>
      <p className="quiet-note">{t('help')}</p>
      <div aria-live="polite">
        {status.state === 'loading' && <p>{t('checking')}</p>}
        {status.state === 'error' && <ErrorNotice error={status.error} />}
        {data && <p className={data.reachable ? 'status-ok' : 'transfer-notice'}>{data.reachable ? t('reachable', { version: data.version ?? '?' }) : t('unreachable', { endpoint: data.endpoint })}</p>}
        {notice && <p className="status-ok">{notice}</p>}
        {failure && <ErrorNotice error={failure} />}
      </div>
      <button type="button" className="text-button" onClick={() => { void load() }} disabled={status.state === 'loading'}>{t('refresh')}</button>
      {data?.reachable && <>
        <h4>{t('installed')}</h4>
        {!data.models.length && <p className="quiet-note">{t('noneInstalled')}</p>}
        <ul className="local-model-list">
          {data.models.map((item) => {
            const current = canonical(model) === canonical(item.name)
            return <li key={item.name}>
              <span><strong>{item.name}</strong>{item.size_bytes !== null && <small> · {size(item.size_bytes)}</small>}{current && <small> · {t('selected')}</small>}</span>
              <span className="row-actions">
                <button type="button" className="button secondary" disabled={current} onClick={() => onSelect(item.name)}>{t('use')}</button>
                <button type="button" className="text-button" disabled={running} onClick={() => { void remove(item.name) }}>{t('remove')}</button>
              </span>
            </li>
          })}
        </ul>
        {pull && <div className="local-model-pull" role="status">
          <p><strong>{pull.model}</strong> — {pull.status === 'running' ? t(`phase_${pull.phase}`, { defaultValue: pull.phase }) : pull.status === 'failed' ? `${t('failed', { model: pull.model })} ${t(`error_${pull.error ?? 'pull_failed'}`, { defaultValue: '' })}` : pull.status === 'cancelled' ? t('cancelled') : t('phase_success')}</p>
          {running && <>
            <progress max={100} value={percent ?? undefined} aria-label={t('phase_downloading')} />
            {pull.total_bytes > 0 && <small>{t('progress', { done: size(pull.completed_bytes), total: size(pull.total_bytes) })}</small>}
            <button type="button" className="button secondary" onClick={() => { void cancel() }}>{t('cancel')}</button>
            <p className="quiet-note">{t('restartNote')}</p>
          </>}
        </div>}
        <h4>{t('suggested')}</h4>
        <p className="quiet-note">{t('suggestedHelp')}</p>
        <ul className="local-model-list">
          {data.suggested.map((item) => <li key={item.id}>
            <span><strong>{item.name}</strong> <code>{item.id}</code><small> · {t('size', { size: size(item.download_bytes) })} · {t('memory', { memory: item.min_memory_gb })}{item.tool_calls ? ` · ${t('tools')}` : ''}</small><small className="block">{t(`note_${item.note}`, { defaultValue: '' })}</small></span>
            {installed.has(canonical(item.id))
              ? <button type="button" className="button secondary" disabled={canonical(model) === canonical(item.id)} onClick={() => onSelect(item.id)}>{t('use')}</button>
              : <button type="button" className="button secondary" disabled={running} onClick={() => { void download(item.id, item) }}>{t('download')}</button>}
          </li>)}
        </ul>
        <label className="field" htmlFor="local-model-other"><span>{t('other')}</span>
          <span className="inline-field"><input id="local-model-other" value={other} onChange={(event) => setOther(event.target.value.trim())} placeholder={t('otherPlaceholder')} maxLength={200} autoComplete="off" aria-describedby="local-model-other-help" />
            <button type="button" className="button secondary" disabled={running || !MODEL.test(other) || other.toLowerCase().includes('cloud')} onClick={() => { void download(other) }}>{t('download')}</button></span>
          <small id="local-model-other-help">{t('otherHelp')}</small></label>
      </>}
    </section>
  )
}
