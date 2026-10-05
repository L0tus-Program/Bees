import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { messages, sendChat } from '../../api/onboarding'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'

export function Chat({ agent }: { agent: AgentSummary & { conversation_id: string } }) {
  const { t, i18n } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const load = useCallback((signal: AbortSignal) => messages(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const { resource, reload } = useResource(load)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (resource.state === 'ready' && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
  }, [resource])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active') return
    setBusy(true); setError(null)
    controller.current = new AbortController()
    try {
      await sendChat(agent.id, resource.data.conversation_id, draft.trim(), controller.current.signal)
      setDraft(''); reload()
    } catch (failure) {
      if (!controller.current.signal.aborted) {
        setError(asApiError(failure))
        // Reconcile persisted history after an uncertain response. Never resend automatically.
        reload()
      }
    } finally { setBusy(false); inputRef.current?.focus() }
  }

  return (
    <section className="surface conversation" aria-labelledby="conversation-heading">
      <div className="conversation-heading"><div><p className="section-label">{t('conversation')}</p><h2 id="conversation-heading">{agent.name}</h2></div><button type="button" className="text-button" onClick={reload} disabled={busy || resource.state === 'loading'}>{t('refreshHistory')}</button></div>
      <p className="conversation-scope">{t('chatScope')}</p>
      {agent.status !== 'active' && <p className="transfer-notice">{bee('inactiveConversation', { status: bee(`status_${agent.status}`) })}</p>}
      {error && <ErrorNotice error={error} />}
      {resource.state === 'loading' && <LoadingNotice />}
      {resource.state === 'error' && <><ErrorNotice error={resource.error} /><button className="button secondary" type="button" onClick={reload}>{t('retry')}</button></>}
      {resource.state === 'ready' && <>
        {(resource.data.has_more || resource.data.messages.length > 200) && <p className="quiet-note">{t('limitedHistory')}</p>}
        <div className="message-list" ref={listRef} role="log" aria-label={t('conversationHistory')} aria-live="polite" aria-relevant="additions">
          {resource.data.messages.length === 0 && <div className="empty-conversation"><span aria-hidden="true">✳</span><h3>{t('chatEmptyTitle')}</h3><p>{t('chatEmptyDescription')}</p></div>}
          {resource.data.messages.slice(-200).map((message) => {
            const date = new Date(message.created_at)
            return <article className={`message message-${message.role}`} key={message.id}>
              <div className="message-meta"><span>{message.role === 'user' ? t('you') : message.role === 'assistant' ? agent.name : t('systemMessage')}</span>{!Number.isNaN(date.getTime()) && <time dateTime={message.created_at}>{new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(date)}</time>}</div>
              <p>{message.content || t('noTextContent')}</p>
            </article>
          })}
        </div>
      </>}
      {busy && <p className="chat-pending" role="status">{t('waitingModel')}</p>}
      <form className="chat-composer" onSubmit={(event) => { void submit(event) }}>
        <label htmlFor="chat-input" className="sr-only">{t('messageLabel')}</label>
        <textarea id="chat-input" ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)} disabled={busy || agent.status !== 'active'} placeholder={t('messagePlaceholder')} rows={3} required maxLength={16000} aria-describedby="chat-help" />
        <div className="composer-actions"><small id="chat-help">{t('chatCost')}</small><button className="button primary" type="submit" disabled={busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active'}>{t(busy ? 'sending' : 'send')}</button></div>
      </form>
    </section>
  )
}
