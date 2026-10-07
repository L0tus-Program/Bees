import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { messages, sendChat } from '../../api/onboarding'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'
import { ApprovalPanel } from '../approvals/ApprovalPanel'
import { PolicyPanel } from '../policies/PolicyPanel'
import { isAutonomyCommand } from '../approvals/state'
import { ChatHistory } from './ChatHistory'
import { commandIdentity } from '../tasks/state'
import { composerReducer, emptyComposer } from './composer'

export function Chat({ agent }: { agent: AgentSummary & { conversation_id: string } }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const { t: approvals } = useTranslation('approvals')
  const load = useCallback((signal: AbortSignal) => messages(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const { resource, reload } = useResource(load)
  const [{ draft, allowDelegation }, updateComposer] = useReducer(composerReducer, emptyComposer)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [editingPolicies, setEditingPolicies] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [identity] = useState(commandIdentity)
  const supportsDelegation = agent.provider_config?.capabilities.tool_calls === true
  const controller = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => () => controller.current?.abort(), [])
  function refresh() { reload(); setRefreshVersion((value) => value + 1) }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!busy && isAutonomyCommand(draft)) { setEditingPolicies(true); updateComposer({ type: 'success' }); identity.reset(); return }
    if (busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active') return
    setBusy(true); setError(null)
    controller.current = new AbortController()
    const consent = supportsDelegation && allowDelegation
    const requestId = identity.forPayload({ conversation_id: resource.data.conversation_id, content: draft.trim(), allow_delegation: consent })
    try {
      await sendChat(agent.id, resource.data.conversation_id, draft.trim(), controller.current.signal, consent, requestId)
      updateComposer({ type: 'success' }); identity.reset(); refresh()
    } catch (failure) {
      if (!controller.current.signal.aborted) {
        setError(asApiError(failure))
        updateComposer({ type: 'failure' })
        // Reconcile persisted history after an uncertain response. Never resend automatically.
        refresh()
      }
    } finally { setBusy(false); inputRef.current?.focus() }
  }

  return (
    <section className="surface conversation" aria-labelledby="conversation-heading">
      <div className="conversation-heading"><div><p className="section-label">{t('conversation')}</p><h2 id="conversation-heading">{agent.name}</h2></div><button type="button" className="text-button" onClick={refresh} disabled={busy || resource.state === 'loading'}>{t('refreshHistory')}</button></div>
      <p className="conversation-scope">{t('chatScope')}</p>
      <p className="quiet-note">{approvals('policiesHelp')}</p>
      <button type="button" className="text-button" onClick={() => setEditingPolicies((value) => !value)} disabled={busy}>{approvals(editingPolicies ? 'closePolicies' : 'editPolicies')}</button>
      {editingPolicies && <PolicyPanel agent={agent} />}
      <ApprovalPanel agentId={agent.id} onChanged={reload} />
      {agent.status !== 'active' && <p className="transfer-notice">{bee('inactiveConversation', { status: bee(`status_${agent.status}`) })}</p>}
      {error && <ErrorNotice error={error} />}
      {resource.state === 'loading' && <LoadingNotice />}
      {resource.state === 'error' && <><ErrorNotice error={resource.error} /><button className="button secondary" type="button" onClick={reload}>{t('retry')}</button></>}
      {resource.state === 'ready' && <>
        {(resource.data.has_more || resource.data.messages.length > 200) && <p className="quiet-note">{t('limitedHistory')}</p>}
        <ChatHistory key={`${agent.id}:${agent.conversation_id}`} agent={agent} messages={resource.data.messages} refreshVersion={refreshVersion} />
      </>}
      {busy && <p className="chat-pending" role="status">{t('waitingModel')}</p>}
      <form className="chat-composer" onSubmit={(event) => { void submit(event) }}>
        <label htmlFor="chat-input" className="sr-only">{t('messageLabel')}</label>
        <textarea id="chat-input" ref={inputRef} value={draft} onChange={(event) => updateComposer({ type: 'draft', value: event.target.value })} disabled={busy || agent.status !== 'active'} placeholder={t('messagePlaceholder')} rows={3} required maxLength={16000} aria-describedby="chat-help" />
        {supportsDelegation ? <label className="checkbox-field"><input type="checkbox" checked={allowDelegation} onChange={(event) => updateComposer({ type: 'consent', value: event.target.checked })} disabled={busy || agent.status !== 'active'} /><span>{t('chatAllowDelegation')}<small>{t('chatAllowDelegationHelp')}</small></span></label> : <p className="quiet-note">{t('chatDelegationUnavailable')}</p>}
        <div className="composer-actions"><small id="chat-help">{t('chatCost')}</small><button className="button primary" type="submit" disabled={busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active'}>{t(busy ? 'sending' : 'send')}</button></div>
      </form>
    </section>
  )
}
