import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getDelegations } from '../../api/delegations'
import type { DelegationCollection } from '../../api/delegations'
import type { AgentSummary, Message } from '../../api/onboarding'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { TaskDetail } from '../tasks/TaskDetail'
import { DelegationCard } from './DelegationCard'

const noDelegations: DelegationCollection['delegations'] = []

function mergeDelegations(incoming: DelegationCollection, previous: DelegationCollection) {
  const known = new Map(previous.delegations.map((item) => [item.id, item]))
  return { ...incoming, delegations: incoming.delegations.map((item) => {
    const prior = known.get(item.id)
    return prior && prior.task.revision > item.task.revision ? prior : item
  }) }
}

export function ChatHistory({ agent, messages, refreshVersion }: { agent: AgentSummary & { conversation_id: string }; messages: Message[]; refreshVersion: number }) {
  const { t, i18n } = useTranslation('product')
  const load = useCallback((signal: AbortSignal) => getDelegations(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const monitor = useTaskPolling(load, mergeDelegations)
  const refreshDelegations = monitor.refresh
  const [selected, setSelected] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const previousRefresh = useRef(refreshVersion)
  useEffect(() => { if (previousRefresh.current !== refreshVersion) { previousRefresh.current = refreshVersion; refreshDelegations() } }, [refreshVersion, refreshDelegations])
  const visibleMessages = messages.slice(-200).filter((message) => message.role !== 'tool')
  const delegations = monitor.data?.delegations ?? noDelegations
  useEffect(() => { if (listRef.current && followLatest.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [messages, delegations])
  const messageIds = new Set(visibleMessages.map((message) => message.id))
  const attached = (id: string) => delegations.filter((item) => (messageIds.has(item.response_message_id) ? item.response_message_id : item.source_message_id) === id)
  const recovered = delegations.filter((item) => !messageIds.has(item.source_message_id) && !messageIds.has(item.response_message_id))
  const card = (item: typeof delegations[number]) => <DelegationCard key={item.id} task={item.task} selected={selected === item.id} onSelect={() => setSelected((current) => current === item.id ? null : item.id)} />
  return <>
    {monitor.error && <p className="form-error" role="status">{t('chatDelegationReadFailed')} <button type="button" className="text-button" onClick={monitor.refresh}>{t('refreshState')}</button></p>}
    {monitor.data?.has_more && <p className="quiet-note">{t('chatDelegationLimited')}</p>}
    <div className="message-list" ref={listRef} onScroll={(event) => { const list = event.currentTarget; followLatest.current = list.scrollHeight - list.scrollTop - list.clientHeight < 40 }} role="log" aria-label={t('conversationHistory')} aria-live="polite" aria-relevant="additions">
      {visibleMessages.length === 0 && <div className="empty-conversation"><span aria-hidden="true">✳</span><h3>{t('chatEmptyTitle')}</h3><p>{t('chatEmptyDescription')}</p></div>}
      {visibleMessages.map((message) => {
        const date = new Date(message.created_at)
        const linked = attached(message.id)
        const rejected = message.delegation_results?.filter((item) => item.status === 'rejected') ?? []
        return <div key={message.id} className="chat-entry">
          {!rejected.length && (message.content || !linked.length) && <article className={`message message-${message.role}`}>
            <div className="message-meta"><span>{message.role === 'user' ? t('you') : message.role === 'assistant' ? agent.name : t('systemMessage')}</span>{!Number.isNaN(date.getTime()) && <time dateTime={message.created_at}>{new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(date)}</time>}</div>
            <p>{message.content || t('noTextContent')}</p>
          </article>}
          {rejected.map((item) => <p key={item.tool_call_id} className="transfer-notice" role="status">{t(item.code === 'one_task_per_turn' ? 'chatDelegationRejectedTurn' : item.code === 'pending_task_limit' ? 'chatDelegationRejectedLimit' : item.code === 'invalid_task_arguments' ? 'chatDelegationRejectedArguments' : ['delegation_policy_denied', 'delegation_approval_required'].includes(item.code ?? '') ? 'chatDelegationRejectedPolicy' : 'chatDelegationRejected')}</p>)}
          {linked.map(card)}
        </div>
      })}
      {recovered.length > 0 && <div><p className="quiet-note">{t('chatDelegationRecovered')}</p>{recovered.map(card)}</div>}
    </div>
    {selected && <TaskDetail key={selected} agent={agent} taskId={selected} onRefresh={monitor.refresh} onClose={() => setSelected(null)} />}
  </>
}
