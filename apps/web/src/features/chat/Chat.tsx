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
import { enableRecommendedTools, getAgentCapabilities, sendAgentMessage } from '../../api/agent'
import type { AgentCapabilities } from '../../api/agent'
import { useDictation } from '../voice/useDictation'
import { canSpeak, readVoicePreference, speak, writeVoicePreference } from '../voice/speech'

export function Chat({ agent }: { agent: AgentSummary & { conversation_id: string } }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const { t: approvals } = useTranslation('approvals')
  const { t: artifacts } = useTranslation('artifacts')
  const { t: agentText } = useTranslation('agent')
  const { t: voice, i18n } = useTranslation('voice')
  const [capabilities, setCapabilities] = useState<AgentCapabilities | null>(null)
  const [agentMode, setAgentMode] = useState<boolean | null>(null)
  const [research, setResearch] = useState(false)
  const [enabling, setEnabling] = useState(false)
  const [capabilityVersion, setCapabilityVersion] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    getAgentCapabilities(agent.id, controller.signal).then((value) => { setCapabilities(value); setAgentMode((current) => current ?? value.available) }, () => { if (!controller.signal.aborted) setCapabilities(null) })
    return () => controller.abort()
  }, [agent.id, agent.revision, capabilityVersion])
  const useAgent = !!capabilities?.available && agentMode !== false
  const agentIdentity = useRef<{ content: string; id: string } | null>(null)
  async function enableTools() {
    setEnabling(true); setError(null)
    try { await enableRecommendedTools(agent.id, crypto.randomUUID()); setAgentMode(true); setCapabilityVersion((value) => value + 1) } catch (failure) { setError(asApiError(failure)) } finally { setEnabling(false) }
  }
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
  const draftRef = useRef(draft)
  useEffect(() => { draftRef.current = draft }, [draft])
  const dictation = useDictation(i18n.resolvedLanguage ?? 'pt-BR', (text) => {
    if (!text) return
    const current = draftRef.current
    updateComposer({ type: 'draft', value: current ? `${current.trimEnd()} ${text}` : text })
  })
  const [voiceReplies, setVoiceReplies] = useState(readVoicePreference)
  const spoken = useRef<string | null | undefined>(undefined)
  useEffect(() => {
    if (resource.state !== 'ready') return
    const last = [...resource.data.messages].reverse().find((item) => item.role === 'assistant' && item.content)
    // Na primeira carga só marca o ponto de partida; lê apenas respostas novas.
    if (spoken.current !== undefined && voiceReplies && last && last.id !== spoken.current) speak(last.content ?? '', i18n.resolvedLanguage ?? 'pt-BR')
    spoken.current = last?.id ?? null
  }, [resource, voiceReplies, i18n.resolvedLanguage])
  function refresh() { reload(); setRefreshVersion((value) => value + 1) }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!busy && isAutonomyCommand(draft)) { setEditingPolicies(true); updateComposer({ type: 'success' }); identity.reset(); return }
    if (busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active' || !filesAllowed) return
    setBusy(true); setError(null)
    controller.current = new AbortController()
    if (useAgent && selectedFiles.length === 0 && !allowRoutineProposal) {
      const content = draft.trim()
      // Mesmo UUID enquanto o texto não mudar: reenvio reconcilia sem criar outra tarefa.
      if (agentIdentity.current?.content !== `${research}:${content}`) agentIdentity.current = { content: `${research}:${content}`, id: crypto.randomUUID() }
      try {
        await sendAgentMessage(agent.id, resource.data.conversation_id, content, agentIdentity.current.id, controller.current.signal, research ? 'research' : 'agent')
        if (controller.current.signal.aborted) return
        agentIdentity.current = null
        updateComposer({ type: 'success' }); refresh()
      } catch (failure) {
        if (!controller.current.signal.aborted) { setError(asApiError(failure)); updateComposer({ type: 'failure' }); refresh() }
      } finally { setBusy(false); inputRef.current?.focus() }
      return
    }
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
        <ChatHistory key={`${agent.id}:${agent.conversation_id}`} agent={agent} messages={resource.data.messages} refreshVersion={refreshVersion} onAgentResult={reload} />
      </>}
      {busy && <p className="chat-pending" role="status">{t('waitingModel')}</p>}
      <ConversationArtifacts agentId={agent.id} conversationId={agent.conversation_id} selected={selectedFiles} onSelected={(items) => { setSelectedFiles(items); setFileConsentRevision(null) }} onReady={setFilesReady} blocked={busy || agent.status !== 'active'} />
      <form className="chat-composer" onSubmit={(event) => { void submit(event) }}>
        <label htmlFor="chat-input" className="sr-only">{t('messageLabel')}</label>
        <textarea id="chat-input" ref={inputRef} value={draft} onChange={(event) => { if (failed && event.target.value !== draft) { setFileConsentRevision(null); setAllowRoutineProposal(false) } updateComposer({ type: 'draft', value: event.target.value }) }} disabled={busy || agent.status !== 'active'} placeholder={t('messagePlaceholder')} rows={3} required maxLength={16000} aria-describedby="chat-help" />
        {selectedFiles.length > 0 && (agent.provider_config ? <label className="checkbox-field"><input type="checkbox" checked={attachmentConsent} disabled={busy || !filesReady || agent.status !== 'active'} onChange={(event) => setFileConsentRevision(event.target.checked ? agent.revision : null)} /><span>{artifacts('consentUse')}<small>{artifacts('consentHelp', { model: agent.provider_config.model, endpoint: agent.provider_config.endpoint })}</small></span></label> : <p className="quiet-note">{artifacts('noDestination')}</p>)}
        {capabilities?.available && <fieldset className="agent-mode"><legend>{agentText('modeLabel')}</legend>
          <label><input type="radio" name="agent-mode" checked={useAgent && !research} onChange={() => { setAgentMode(true); setResearch(false) }} disabled={busy} /> {agentText('modeAgent')}</label>
          <label><input type="radio" name="agent-mode" checked={useAgent && research} onChange={() => { setAgentMode(true); setResearch(true) }} disabled={busy} /> {agentText('modeResearch')}</label>
          <label><input type="radio" name="agent-mode" checked={!useAgent} onChange={() => setAgentMode(false)} disabled={busy} /> {agentText('modeChat')}</label>
          <small>{agentText(!useAgent ? 'modeChatHelp' : research ? 'modeResearchHelp' : 'modeAgentHelp')}</small>
          {useAgent && capabilities.worker && !capabilities.worker.available && <small className="transfer-notice">{agentText('workerOffline')}</small>}
        </fieldset>}
        {capabilities && !capabilities.available && capabilities.tool_calls_supported && !capabilities.recommended_installed && <div className="agent-enable"><strong>{agentText('enableTitle')}</strong><small>{agentText('enableHelp')}</small><button type="button" className="button secondary" onClick={() => { void enableTools() }} disabled={enabling || busy}>{agentText(enabling ? 'enabling' : 'enableButton')}</button></div>}
        {capabilities && !capabilities.tool_calls_supported && <p className="quiet-note">{agentText('noToolModel')}</p>}
        {useAgent ? null : supportsDelegation ? <label className="checkbox-field"><input type="checkbox" checked={allowDelegation} onChange={(event) => updateComposer({ type: 'consent', value: event.target.checked })} disabled={busy || agent.status !== 'active'} /><span>{t('chatAllowDelegation')}<small>{t('chatAllowDelegationHelp')}</small></span></label> : <p className="quiet-note">{t('chatDelegationUnavailable')}</p>}
        <label className="checkbox-field"><input type="checkbox" checked={allowRoutineProposal} onChange={(event) => setAllowRoutineProposal(event.target.checked)} disabled={busy || agent.status !== 'active'} /><span>{t('chatRoutineProposal')}<small>{t('chatRoutineProposalHelp')}</small></span></label>
        {(dictation.supported || canSpeak()) && <div className="voice-controls">
          {dictation.supported && <button type="button" className="button secondary" aria-pressed={dictation.listening} disabled={busy || agent.status !== 'active'} onClick={() => { if (dictation.listening) { dictation.stop() } else { dictation.start() } }}>{voice(dictation.listening ? 'stopDictation' : 'dictate')}</button>}
          {dictation.listening && <small role="status">{dictation.interim || voice('listening')}</small>}
          {dictation.error && <small className="transfer-notice">{voice(`error_${dictation.error}`, { defaultValue: voice('error_generic') })}</small>}
          {canSpeak() && <label className="checkbox-field"><input type="checkbox" checked={voiceReplies} onChange={(event) => { setVoiceReplies(event.target.checked); writeVoicePreference(event.target.checked) }} /><span>{voice('voiceReplies')}<small>{voice('voiceRepliesHelp')}</small></span></label>}
          {dictation.supported && <small className="quiet-note">{voice('dictationHelp')}</small>}
        </div>}
        <div className="composer-actions"><small id="chat-help">{t('chatCost')}</small><button className="button primary" type="submit" disabled={busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active' || !filesAllowed}>{t(busy ? 'sending' : 'send')}</button></div>
      </form>
    </section>
  )
}
