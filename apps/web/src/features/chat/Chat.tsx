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
import { ConversationArtifacts } from '../artifacts/ConversationArtifacts'
import { isAttachmentSelection } from '../artifacts/mutations'
import type { ArtifactRecord } from '../artifacts/api'

export function Chat({ agent }: { agent: AgentSummary & { conversation_id: string } }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const { t: approvals } = useTranslation('approvals')
  const { t: artifacts } = useTranslation('artifacts')
  const load = useCallback((signal: AbortSignal) => messages(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const { resource, reload } = useResource(load)
  const [{ draft, allowDelegation, failed }, updateComposer] = useReducer(composerReducer, emptyComposer)
  const [selectedFiles, setSelectedFiles] = useState<ArtifactRecord[]>([])
  const [fileConsentRevision, setFileConsentRevision] = useState<number | null>(null)
  const [filesReady, setFilesReady] = useState(false)
  const [allowRoutineProposal, setAllowRoutineProposal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [editingPolicies, setEditingPolicies] = useState(false)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [identity] = useState(commandIdentity)
  const supportsDelegation = agent.provider_config?.capabilities.tool_calls === true
  const controller = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const attachmentConsent = fileConsentRevision === agent.revision && selectedFiles.length > 0
  const filesAllowed = selectedFiles.length === 0 || filesReady && attachmentConsent && !!agent.provider_config && isAttachmentSelection(selectedFiles, agent.id, agent.conversation_id)
  useEffect(() => () => controller.current?.abort(), [])
  function refresh() { reload(); setRefreshVersion((value) => value + 1) }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!busy && isAutonomyCommand(draft)) { setEditingPolicies(true); updateComposer({ type: 'success' }); identity.reset(); return }
    if (busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active' || !filesAllowed) return
    setBusy(true); setError(null)
    controller.current = new AbortController()
    const consent = supportsDelegation && allowDelegation
    const attachmentUse = selectedFiles.length > 0 ? { attachments: selectedFiles.map((item) => ({ artifact_id: item.id, revision: item.revision, sha256: item.sha256! })), attachment_consent: { expected_agent_revision: agent.revision } } : undefined
    const requestId = identity.forPayload({ conversation_id: resource.data.conversation_id, content: draft.trim(), allow_delegation: consent, ...(allowRoutineProposal ? { allow_routine_proposal: true } : {}), ...(attachmentUse ?? {}) })
    try {
      await sendChat(agent.id, resource.data.conversation_id, draft.trim(), controller.current.signal, consent, requestId, attachmentUse, allowRoutineProposal)
      if (controller.current.signal.aborted) return
      updateComposer({ type: 'success' }); setAllowRoutineProposal(false); setSelectedFiles([]); setFileConsentRevision(null); identity.reset(); refresh()
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
      <ConversationArtifacts agentId={agent.id} conversationId={agent.conversation_id} selected={selectedFiles} onSelected={(items) => { setSelectedFiles(items); setFileConsentRevision(null) }} onReady={setFilesReady} blocked={busy || agent.status !== 'active'} />
      <form className="chat-composer" onSubmit={(event) => { void submit(event) }}>
        <label htmlFor="chat-input" className="sr-only">{t('messageLabel')}</label>
        <textarea id="chat-input" ref={inputRef} value={draft} onChange={(event) => { if (failed && event.target.value !== draft) { setFileConsentRevision(null); setAllowRoutineProposal(false) } updateComposer({ type: 'draft', value: event.target.value }) }} disabled={busy || agent.status !== 'active'} placeholder={t('messagePlaceholder')} rows={3} required maxLength={16000} aria-describedby="chat-help" />
        {selectedFiles.length > 0 && (agent.provider_config ? <label className="checkbox-field"><input type="checkbox" checked={attachmentConsent} disabled={busy || !filesReady || agent.status !== 'active'} onChange={(event) => setFileConsentRevision(event.target.checked ? agent.revision : null)} /><span>{artifacts('consentUse')}<small>{artifacts('consentHelp', { model: agent.provider_config.model, endpoint: agent.provider_config.endpoint })}</small></span></label> : <p className="quiet-note">{artifacts('noDestination')}</p>)}
        {supportsDelegation ? <label className="checkbox-field"><input type="checkbox" checked={allowDelegation} onChange={(event) => updateComposer({ type: 'consent', value: event.target.checked })} disabled={busy || agent.status !== 'active'} /><span>{t('chatAllowDelegation')}<small>{t('chatAllowDelegationHelp')}</small></span></label> : <p className="quiet-note">{t('chatDelegationUnavailable')}</p>}
        <label className="checkbox-field"><input type="checkbox" checked={allowRoutineProposal} onChange={(event) => setAllowRoutineProposal(event.target.checked)} disabled={busy || agent.status !== 'active'} /><span>{t('chatRoutineProposal')}<small>{t('chatRoutineProposalHelp')}</small></span></label>
        <div className="composer-actions"><small id="chat-help">{t('chatCost')}</small><button className="button primary" type="submit" disabled={busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active' || !filesAllowed}>{t(busy ? 'sending' : 'send')}</button></div>
      </form>
    </section>
  )
}
