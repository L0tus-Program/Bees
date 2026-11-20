import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError, isTemporaryReadFailure } from '../../api/client'
import type { ApiError } from '../../api/client'
import { cancelLocalPull, deleteLocalModel, formatBytes, getLocalModels, getLocalPull, getLocalPullRequest, startLocalPull } from '../../api/localModels'
import type { LocalModelPull, LocalModelStatus, SuggestedLocalModel } from '../../api/localModels'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'
import { clearPullIntent, preservePullIntent, readPullRecovery } from './pullRecovery'
import type { PullIntent } from './pullRecovery'
import { clearRemovalIntent, preserveRemovalIntent, readRemovalRecovery } from './removalRecovery'

type State = { state: 'loading' } | { state: 'ready'; data: LocalModelStatus } | { state: 'error'; error: ApiError }
type Notice = { kind: 'download' | 'model'; text: string }
const MODEL = /^[a-z0-9][a-z0-9._-]{0,99}(\/[a-z0-9][a-z0-9._-]{0,99})?(:[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/
const canonical = (value: string) => value.split('/').pop()?.includes(':') ? value : `${value}:latest`

type Props = { endpoint: string; model: string; onSelect: (model: string) => void }

export function LocalModelPanel(props: Props) {
  // Uma troca de destino começa uma sessão nova; callbacks antigos conservam seu destino.
  return <LocalModelSession key={props.endpoint} {...props} />
}

function LocalModelSession({ endpoint, model, onSelect }: Props) {
  const { t, i18n } = useTranslation('localModel')
  const [status, setStatus] = useState<State>({ state: 'loading' })
  const [pull, setPull] = useState<LocalModelPull | null>(null)
  const [other, setOther] = useState('')
  const [notice, setNotice] = useState<Notice | null>(null)
  const [retry, setRetry] = useState(0)
  const [pausedPull, setPausedPull] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [pending, setPending] = useState(false)
  const [recovery, setRecovery] = useState(() => readPullRecovery(endpoint))
  const [removalRecovery, setRemovalRecovery] = useState(() => readRemovalRecovery(endpoint))
  const unresolvedPull = recovery.blocked || recovery.intent !== null
  const unresolvedRemoval = removalRecovery.blocked || removalRecovery.intent !== null
  const unresolved = unresolvedPull || unresolvedRemoval
  const reading = useRef<AbortController | null>(null)
  const operation = useRef<AbortController | null>(null)
  const target = endpoint || undefined
  const size = (value: number) => formatBytes(value, i18n.language)

  const load = useCallback(async () => {
    reading.current?.abort()
    const controller = new AbortController(); reading.current = controller
    setStatus({ state: 'loading' })
    try {
      const data = await getLocalModels(target, controller.signal)
      if (controller.signal.aborted) return
      setStatus({ state: 'ready', data })
      setPull((current) => current?.status === 'running' ? current
        : data.pulls.find((item) => item.status === 'running' && item.id !== current?.id) ?? current)
    } catch (error) { if (!controller.signal.aborted) setStatus({ state: 'error', error: asApiError(error) }) }
  }, [target])

  useEffect(() => {
    void load()
    return () => reading.current?.abort()
  }, [load])
  useEffect(() => () => operation.current?.abort(), [])

  useEffect(() => {
    if (pull?.status === 'running') setNotice((current) => current?.kind === 'download' ? null : current)
  }, [pull?.id, pull?.status])

  useEffect(() => {
    if (!pull || pull.status !== 'running' || pending || pausedPull === pull.id) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      getLocalPull(pull, controller.signal).then((next) => {
        if (controller.signal.aborted) return
        setFailure(null); setRetry(0); setPull(next)
        if (next.status === 'completed') { setNotice({ kind: 'download', text: t('completed', { model: next.model }) }); void load() }
      }).catch((error) => {
        if (controller.signal.aborted) return
        const failure = asApiError(error)
        // Após reinício do Bees o acompanhamento some; a lista mostra o que o Ollama já tem.
        if (failure.code === 'pull_not_found') { setFailure(null); setRetry(0); setPull(null); setNotice({ kind: 'download', text: t('interrupted', { model: pull.model }) }); void load(); return }
        setFailure(failure)
        if (failure.code !== 'invalid_response' && isTemporaryReadFailure(failure)) setRetry((value) => value + 1)
        else setPausedPull(pull.id)
      })
    }, retry ? Math.min(1000 * 2 ** retry, 15000) : 1000)
    return () => { clearTimeout(timer); controller.abort() }
  }, [pull, retry, load, t, pending, pausedPull])

  function beginOperation(recovering = false) {
    if (operation.current || (!recovering && status.state !== 'ready')) return null
    const controller = new AbortController(); operation.current = controller
    setPending(true); setFailure(null)
    return controller
  }
  function finishOperation(controller: AbortController) {
    if (operation.current !== controller) return
    operation.current = null
    if (!controller.signal.aborted) setPending(false)
  }

  function acceptDownload(next: LocalModelPull) {
    setPull(next); setRetry(0); setPausedPull(null); setFailure(null)
    if (clearPullIntent(endpoint)) setRecovery({ intent: null, blocked: false })
    else setRecovery((current) => ({ ...current, blocked: true }))
    if (next.status === 'completed') { setNotice({ kind: 'download', text: t('completed', { model: next.model }) }); void load() }
  }

  async function recoverDownload() {
    const intent = recovery.intent
    if (!intent) return
    const controller = beginOperation(true); if (!controller) return
    try {
      const next = await getLocalPullRequest(intent.endpoint || undefined, intent.model, intent.clientRequestId, controller.signal)
      if (!controller.signal.aborted) acceptDownload(next)
    } catch (error) { if (!controller.signal.aborted) setFailure(asApiError(error)) }
    finally { finishOperation(controller) }
  }

  async function releaseRecovery() {
    const controller = beginOperation(true); if (!controller) return
    try {
      if (!await confirmAction(t('releaseConfirm')) || controller.signal.aborted) return
      if (clearPullIntent(endpoint)) setRecovery({ intent: null, blocked: false })
      else setRecovery((current) => ({ ...current, blocked: true }))
    } finally { finishOperation(controller) }
  }

  async function releaseRemoval() {
    const controller = beginOperation(true); if (!controller) return
    try {
      if (!await confirmAction(t('releaseRemovalConfirm')) || controller.signal.aborted) return
      if (clearRemovalIntent(endpoint)) setRemovalRecovery({ intent: null, blocked: false })
      else setRemovalRecovery((current) => ({ ...current, blocked: true }))
    } finally { finishOperation(controller) }
  }

  function hasStoredMutation() {
    const stored = readPullRecovery(endpoint)
    const removal = readRemovalRecovery(endpoint)
    setRecovery(stored); setRemovalRecovery(removal)
    return stored.blocked || stored.intent !== null || removal.blocked || removal.intent !== null
  }

  async function download(name: string, suggestion?: SuggestedLocalModel) {
    if (unresolved) return
    const message = suggestion
      ? t('confirmKnown', { model: name, size: size(suggestion.download_bytes), source: suggestion.source, endpoint: status.state === 'ready' ? status.data.endpoint : endpoint, memory: suggestion.min_memory_gb })
      : t('confirmUnknown', { model: name, endpoint: status.state === 'ready' ? status.data.endpoint : endpoint })
    const controller = beginOperation(); if (!controller) return
    try {
      if (!await confirmAction(message) || controller.signal.aborted) return
      setNotice(null)
      if (hasStoredMutation()) return
      const intent: PullIntent = { endpoint, model: name, clientRequestId: crypto.randomUUID() }
      if (!preservePullIntent(intent)) { setRecovery({ intent: null, blocked: true }); return }
      setRecovery({ intent, blocked: false })
      const next = await startLocalPull(target, name, intent.clientRequestId, controller.signal)
      if (!controller.signal.aborted) acceptDownload(next)
    } catch (error) { if (!controller.signal.aborted) setFailure(asApiError(error)) }
    finally { finishOperation(controller) }
  }

  async function cancel() {
    if (!pull) return
    const controller = beginOperation(); if (!controller) return
    try {
      const next = await cancelLocalPull(pull, controller.signal)
      if (!controller.signal.aborted) {
        setRetry(0); setPausedPull(null); setPull(next)
        const message = next.status === 'cancelled' ? t('cancelled') : next.status === 'completed' ? t('completed', { model: next.model }) : null
        setNotice(message ? { kind: 'download', text: message } : null)
        if (next.status === 'completed') await load()
      }
    } catch (error) { if (!controller.signal.aborted) setFailure(asApiError(error)) }
    finally { finishOperation(controller) }
  }

  async function remove(name: string) {
    if (unresolved) return
    const controller = beginOperation(); if (!controller) return
    try {
      if (!await confirmAction(t('removeConfirm', { model: name })) || controller.signal.aborted) return
      setNotice(null)
      if (hasStoredMutation()) return
      const intent = { endpoint, model: name }
      if (!preserveRemovalIntent(intent)) { setRemovalRecovery({ intent: null, blocked: true }); return }
      setRemovalRecovery({ intent, blocked: false })
      await deleteLocalModel(target, name, controller.signal)
      if (!controller.signal.aborted) {
        if (clearRemovalIntent(endpoint)) setRemovalRecovery({ intent: null, blocked: false })
        else setRemovalRecovery((current) => ({ ...current, blocked: true }))
        setNotice({ kind: 'model', text: t('removed') }); await load()
      }
    } catch (error) { if (!controller.signal.aborted) setFailure(asApiError(error)) }
    finally { finishOperation(controller) }
  }

  const data = status.state === 'ready' ? status.data : null
  const installed = new Set(data?.models.map((item) => canonical(item.name)) ?? [])
  const running = pull?.status === 'running'
  const percent = pull && pull.total_bytes ? Math.floor((pull.completed_bytes / pull.total_bytes) * 100) : null
  return (
    <section className="local-model" aria-labelledby="local-model-title" aria-busy={pending}>
      <h3 id="local-model-title">{t('title')}</h3>
      <p className="quiet-note">{t('help')}</p>
      <div aria-live="polite">
        {status.state === 'loading' && <p>{t('checking')}</p>}
        {status.state === 'error' && <ErrorNotice error={status.error} />}
        {data && <p className={data.reachable ? 'status-ok' : 'transfer-notice'}>{data.reachable ? t('reachable', { version: data.version ?? '?' }) : t('unreachable', { endpoint: data.endpoint })}</p>}
        {notice && <p className="status-ok">{notice.text}</p>}
        {failure && <ErrorNotice error={failure} />}
      </div>
      <button type="button" className="text-button" onClick={() => { void load() }} disabled={pending || status.state === 'loading'}>{t('refresh')}</button>
      {unresolvedPull && <div className="transfer-notice" role="status">
        <p>{recovery.intent ? t('pendingDownload', { model: recovery.intent.model, endpoint: recovery.intent.endpoint || 'http://127.0.0.1:11434' }) : t('recoveryBlocked')}</p>
        {recovery.blocked && recovery.intent && <p>{t('recoveryBlocked')}</p>}
        {recovery.intent && <button type="button" className="button secondary" disabled={pending} onClick={() => { void recoverDownload() }}>{t('checkRequest')}</button>}
        <button type="button" className="text-button" disabled={pending} onClick={() => { void releaseRecovery() }}>{t('releaseRequest')}</button>
      </div>}
      {unresolvedRemoval && <div className="transfer-notice" role="status">
        <p>{removalRecovery.intent ? t('pendingRemoval', { model: removalRecovery.intent.model, endpoint: removalRecovery.intent.endpoint || 'http://127.0.0.1:11434' }) : t('removalRecoveryBlocked')}</p>
        {removalRecovery.blocked && removalRecovery.intent && <p>{t('removalRecoveryBlocked')}</p>}
        <button type="button" className="text-button" disabled={pending} onClick={() => { void releaseRemoval() }}>{t('releaseRemoval')}</button>
      </div>}
      {data?.reachable && <>
        <h4>{t('installed')}</h4>
        {!data.models.length && <p className="quiet-note">{t('noneInstalled')}</p>}
        <ul className="local-model-list">
          {data.models.map((item) => {
            const current = canonical(model) === canonical(item.name)
            return <li key={item.name}>
              <span><strong>{item.name}</strong>{item.size_bytes !== null && <small> · {size(item.size_bytes)}</small>}{current && <small> · {t('selected')}</small>}</span>
              <span className="row-actions">
                <button type="button" className="button secondary" disabled={current || pending} onClick={() => onSelect(item.name)}>{t('use')}</button>
                <button type="button" className="text-button" disabled={running || pending || unresolved} onClick={() => { void remove(item.name) }}>{t('remove')}</button>
              </span>
            </li>
          })}
        </ul>
      </>}
      {pull && <div className="local-model-pull" role="status">
        <p><strong>{pull.model}</strong> — {pull.status === 'running' ? t(pull.phase === 'success' ? 'awaitingCompletion' : `phase_${pull.phase}`, { defaultValue: pull.phase }) : pull.status === 'failed' ? `${t('failed', { model: pull.model })} ${t(`error_${pull.error ?? 'pull_failed'}`, { defaultValue: '' })}` : pull.status === 'cancelled' ? t('cancelled') : t('phase_success')}</p>
        {running && <>
          <progress max={100} value={percent ?? undefined} aria-label={t('phase_downloading')} />
          {pull.total_bytes > 0 && <small>{t('progress', { done: size(pull.completed_bytes), total: size(pull.total_bytes) })}</small>}
          {pausedPull === pull.id && <>
            <p className="transfer-notice">{t('trackingPaused')}</p>
            <button type="button" className="button secondary" disabled={pending} onClick={() => { setFailure(null); setRetry(0); setPausedPull(null) }}>{t('resumeTracking')}</button>
          </>}
          <button type="button" className="button secondary" disabled={pending || status.state !== 'ready'} onClick={() => { void cancel() }}>{t('cancel')}</button>
          <p className="quiet-note">{t('restartNote')}</p>
        </>}
      </div>}
      {data?.reachable && <>
        <h4>{t('suggested')}</h4>
        <p className="quiet-note">{t('suggestedHelp')}</p>
        <ul className="local-model-list">
          {data.suggested.map((item) => <li key={item.id}>
            <span><strong>{item.name}</strong> <code>{item.id}</code><small> · {t('size', { size: size(item.download_bytes) })} · {t('memory', { memory: item.min_memory_gb })}{item.tool_calls ? ` · ${t('tools')}` : ''}</small><small className="block">{t(`note_${item.note}`, { defaultValue: '' })}</small></span>
            {installed.has(canonical(item.id))
              ? <button type="button" className="button secondary" disabled={pending || canonical(model) === canonical(item.id)} onClick={() => onSelect(item.id)}>{t('use')}</button>
              : <button type="button" className="button secondary" disabled={running || pending || unresolved} onClick={() => { void download(item.id, item) }}>{t('download')}</button>}
          </li>)}
        </ul>
        <label className="field" htmlFor="local-model-other"><span>{t('other')}</span>
          <span className="inline-field"><input id="local-model-other" value={other} onChange={(event) => setOther(event.target.value.trim())} placeholder={t('otherPlaceholder')} maxLength={200} autoComplete="off" aria-describedby="local-model-other-help" />
            <button type="button" className="button secondary" disabled={running || pending || unresolved || !MODEL.test(other) || other.toLowerCase().includes('cloud')} onClick={() => { void download(other) }}>{t('download')}</button></span>
          <small id="local-model-other-help">{t('otherHelp')}</small></label>
      </>}
    </section>
  )
}
