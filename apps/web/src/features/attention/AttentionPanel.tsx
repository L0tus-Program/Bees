import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getAttention, getAttentionPreferences, setAttentionPreference, setAttentionRead } from '../../api/attention'
import type { AttentionItem, AttentionPage, AttentionPreference, PreferenceCommand, ReadCommand } from '../../api/attention'
import { ApiError, asApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { TaskDetail } from '../tasks/TaskDetail'
import { TaskTime } from '../tasks/TaskTime'
import { mergeAttention } from './state'
import './attention.css'

type Pending = { kind: 'read'; item: AttentionItem; body: ReadCommand } | { kind: 'preference'; preference: AttentionPreference; body: PreferenceCommand }
interface Snapshot { page: AttentionPage; preferences: AttentionPreference[] }

export function AttentionList({ items, disabled = false, onOpen, onRead }: { items: AttentionItem[]; disabled?: boolean; onOpen: (taskId: string) => void; onRead: (item: AttentionItem) => void }) {
  const { t } = useTranslation('attention')
  return <ul className="attention-list">{items.map((item) => <li className="attention-card" key={item.id}>
    <h3>{t(`kinds.${item.kind}`)}</h3>
    <p>{item.task_title}</p>
    <div className="attention-meta"><TaskTime value={item.created_at} /><span>{t(item.read ? 'read' : 'unreadLabel')}</span>{item.muted && <span>{t('muted')}</span>}</div>
    {item.requires_attention && (item.kind !== 'task_completed' || !item.read) && <p className="transfer-notice">{t('pending')}</p>}
    {item.kind === 'approval_required' && !item.requires_attention && <p className="quiet-note">{t('resolved')}</p>}
    {item.kind === 'outcome_unknown' && <p className="quiet-note">{t('unknownHelp')}</p>}
    <div className="attention-actions"><button className="button secondary" type="button" disabled={disabled} onClick={() => onOpen(item.task_id)}>{t('openTask')}</button><button className="text-button" type="button" disabled={disabled} onClick={() => onRead(item)}>{t(item.read ? 'markUnread' : 'markRead')}</button></div>
  </li>)}</ul>
}

export function AttentionPanel({ agent }: { agent: AgentSummary }) {
  const { t } = useTranslation('attention')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const stored = useRef<Snapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<'saved' | 'conflict' | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [lost, setLost] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const reader = useRef<AbortController | null>(null)
  const writer = useRef<AbortController | null>(null)
  const saveSnapshot = (value: Snapshot) => { stored.current = value; setSnapshot(value) }

  const load = useCallback(async (before?: number) => {
    reader.current?.abort()
    const controller = new AbortController(); reader.current = controller
    setLoading(true)
    try {
      const [page, preferences] = await Promise.all([getAttention(agent.id, controller.signal, before), getAttentionPreferences(agent.id, controller.signal)])
      if (controller.signal.aborted) return
      const old = stored.current
      if (old) {
        for (const preference of preferences) {
          const previous = old.preferences.find((value) => value.kind === preference.kind)
          if (previous && (preference.revision < previous.revision || (preference.revision === previous.revision && preference.enabled !== previous.enabled))) throw new ApiError('invalid_response')
        }
      }
      saveSnapshot({ page: { ...page, items: mergeAttention(old?.page.items ?? [], page.items, before !== undefined) }, preferences })
      setError(null)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [agent.id])
  useEffect(() => { void load(); return () => { reader.current?.abort(); writer.current?.abort() } }, [load])

  async function send(intent: Pending) {
    if (busy) return
    reader.current?.abort()
    const controller = new AbortController(); writer.current = controller
    setPending(intent); setBusy(true); setLost(false); setError(null); setNotice(null)
    try {
      const old = stored.current
      if (!old) return
      if (intent.kind === 'read') {
        const item = await setAttentionRead(agent.id, intent.item, intent.body, controller.signal)
        if (controller.signal.aborted) return
        const items = old.page.items.map((value) => value.id === item.id ? item : value)
        saveSnapshot({ ...old, page: { ...old.page, items } })
      } else {
        const preference = await setAttentionPreference(agent.id, intent.preference.kind, intent.body, controller.signal)
        if (controller.signal.aborted) return
        saveSnapshot({ ...old, preferences: old.preferences.map((value) => value.kind === preference.kind ? preference : value) })
      }
      setPending(null); setNotice('saved'); void load()
    } catch (failure) {
      if (controller.signal.aborted) return
      const apiError = asApiError(failure)
      if (apiError.status === 409) { setPending(null); setNotice('conflict'); void load() }
      else { setLost(true); setError(apiError) }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  function mark(item: AttentionItem) {
    void send({ kind: 'read', item, body: { client_request_id: crypto.randomUUID(), expected_revision: item.revision, read: !item.read } })
  }
  function preference(value: AttentionPreference) {
    void send({ kind: 'preference', preference: value, body: { client_request_id: crypto.randomUUID(), expected_revision: value.revision, enabled: !value.enabled } })
  }
  if (taskId) return <div className="surface"><TaskDetail key={`${agent.id}:${taskId}`} agent={agent} taskId={taskId} onRefresh={() => { void load() }} onClose={() => { setTaskId(null); void load() }} /></div>
  const disabled = busy || loading || !!error || !!pending
  return <section className="surface attention-panel" aria-labelledby="attention-heading" aria-busy={loading || busy}>
    <div className="form-heading"><h2 id="attention-heading">{t('title')}</h2><button className="text-button" type="button" disabled={loading || busy} onClick={() => { void load() }}>{t('refresh')}</button></div>
    <p className="form-introduction">{t('description')}</p>
    {notice && <p className={notice === 'saved' ? 'form-success' : 'form-error'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {error && <><ErrorNotice error={error} />{snapshot && <p className="quiet-note">{t('stale')}</p>}</>}
    {lost && pending && <div className="transfer-notice" role="status"><p>{t('lost')}</p><div className="attention-actions"><button type="button" className="button secondary" disabled={busy || loading} onClick={() => { void send(pending) }}>{t('retry')}</button><button type="button" className="text-button" disabled={busy || loading} onClick={() => { setPending(null); setLost(false); void load() }}>{t('cancel')}</button></div></div>}
    {snapshot && <>
      <p role="status">{t('unread', { count: snapshot.page.unread_count })}</p>
      {snapshot.page.items.length === 0 && !error && <p className="quiet-note">{t('empty')}</p>}
      <AttentionList items={snapshot.page.items} disabled={disabled} onRead={mark} onOpen={setTaskId} />
      {snapshot.page.next_before !== null && <button className="button secondary" type="button" disabled={loading || busy || !!pending} onClick={() => { void load(snapshot.page.next_before!) }}>{t('more')}</button>}
      <details className="attention-preferences"><summary>{t('preferences')}</summary><p className="quiet-note">{t('preferencesHelp')}</p>{snapshot.preferences.map((value) => <div className="attention-preference" key={value.kind}><span>{t(`kinds.${value.kind}`)}</span><button className="button secondary" type="button" disabled={disabled} aria-pressed={value.enabled} aria-label={`${t(value.enabled ? 'disabled' : 'enabled')}: ${t(`kinds.${value.kind}`)}`} onClick={() => preference(value)}>{t(value.enabled ? 'disabled' : 'enabled')}</button></div>)}</details>
    </>}
  </section>
}
