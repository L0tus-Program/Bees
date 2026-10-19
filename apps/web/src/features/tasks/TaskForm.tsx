import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createTask } from '../../api/tasks'
import { deliverableKinds } from '../../api/tasks'
import type { DeliverableKind, TaskRecord } from '../../api/tasks'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { commandIdentity } from './state'
import { checkedToolTaskIntent, createToolTask, getToolTaskCapabilities } from '../../api/toolTasks'
import type { ToolTaskIntent } from '../../api/toolTasks'
import { clearToolTask, preserveToolTask, restoreToolTask } from './toolTaskRecovery'

export function TaskForm({ agent, onCreated, onClose, onRefresh }: { agent: AgentSummary; onCreated: (task: TaskRecord) => void; onClose: () => void; onRefresh: () => void }) {
  const { t } = useTranslation('tasks')
  const { t: tools } = useTranslation('toolTasks')
  const [recovery] = useState(() => restoreToolTask(agent.id))
  const [pending, setPending] = useState<ToolTaskIntent | null>(recovery.intent)
  const [corrupt, setCorrupt] = useState(recovery.corrupt)
  const [toolMode, setToolMode] = useState(false)
  const [toolsAvailable, setToolsAvailable] = useState(false)
  const [storageError, setStorageError] = useState(false)
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const [expectedResult, setExpectedResult] = useState('')
  const [deliverables, setDeliverables] = useState<DeliverableKind[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [identity] = useState(commandIdentity)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { input.current?.focus(); return () => controller.current?.abort() }, [])
  useEffect(() => {
    const read = new AbortController()
    void getToolTaskCapabilities(agent.id, read.signal).then((value) => { if (!read.signal.aborted) setToolsAvailable(value.available) }, () => {})
    return () => read.abort()
  }, [agent.id])
  async function sendToolTask(intent: ToolTaskIntent) {
    if (busy) return
    try { preserveToolTask(intent); setStorageError(false) } catch { setStorageError(true); return }
    setBusy(true); setPending(intent); setError(null)
    const operation = new AbortController(); controller.current = operation
    try {
      const task = await createToolTask(intent, operation.signal)
      if (!operation.signal.aborted) { clearToolTask(agent.id); setPending(null); identity.reset(); onCreated(task) }
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); onRefresh() } }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  function discard() {
    try { clearToolTask(agent.id); setPending(null); setCorrupt(false); setError(null); onRefresh() }
    catch { setStorageError(true) }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || pending || corrupt || !title.trim() || !objective.trim() || !expectedResult.trim()) return
    if (toolMode) {
      if (!toolsAvailable || deliverables.length) return
      try {
        const body = { title: title.trim(), objective: objective.trim(), expected_result: expectedResult.trim(), max_calls: 4, max_active_seconds: 120, deliverables: [] }
        await sendToolTask(checkedToolTaskIntent({ agentId: agent.id, body: { ...body, client_request_id: identity.forPayload({ mode: 'local_text_tools', ...body }) } }, agent.id))
      } catch (failure) { setError(asApiError(failure)) }
      return
    }
    setBusy(true); setError(null)
    const operation = new AbortController(); controller.current = operation
    // Sem arquivos o corpo é idêntico ao anterior; a escolha faz parte da identidade do pedido.
    const files = deliverableKinds.filter((kind) => deliverables.includes(kind))
    const body = { title: title.trim(), objective: objective.trim(), expected_result: expectedResult.trim(), ...(files.length ? { deliverables: files } : {}), ...(agent.conversation_id ? { conversation_id: agent.conversation_id } : {}) }
    try {
      const task = await createTask(agent.id, { ...body, client_request_id: identity.forPayload(body) }, operation.signal)
      if (!operation.signal.aborted) { identity.reset(); onCreated(task) }
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); onRefresh() } }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  if (pending || corrupt) return <section className="task-form" aria-busy={busy}>
    <h3>{t('delegate')}</h3><p role="status" className="transfer-notice">{tools(corrupt ? 'corrupt' : 'pending')}</p>
    {pending && <><h4>{pending.body.title}</h4><p>{pending.body.objective}</p><p className="quiet-note">{tools('private')}</p><button type="button" className="button primary" disabled={busy} onClick={() => { void sendToolTask(pending) }}>{tools('verify')}</button></>}
    {error && <ErrorNotice error={error} />}{storageError && <p role="alert">{tools('storage')}</p>}
    <p className="quiet-note">{tools('discardHelp')}</p><div className="task-actions"><button type="button" className="text-button" disabled={busy} onClick={onRefresh}>{t('refresh')}</button><button type="button" className="text-button" disabled={busy} onClick={discard}>{tools('discard')}</button><button type="button" className="text-button" disabled={busy} onClick={onClose}>{t('close')}</button></div>
  </section>
  return <form className="task-form" onSubmit={(event) => { void submit(event) }} aria-label={t('delegate')}>
    {error && <><ErrorNotice error={error} /><p className="quiet-note">{t('uncertainMutation')}</p><button type="button" className="text-button" onClick={onRefresh}>{t('refresh')}</button></>}
    {storageError && <p role="alert">{tools('storage')}</p>}
    <fieldset disabled={busy}>
      <label className="field" htmlFor="task-title"><span>{t('title')}</span><input id="task-title" ref={input} value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} placeholder={t('titlePlaceholder')} aria-describedby="task-title-help" /><small id="task-title-help">{t('titleHelp')}</small></label>
      <label className="field" htmlFor="task-objective"><span>{t('objective')}</span><textarea id="task-objective" value={objective} onChange={(event) => setObjective(event.target.value)} required rows={3} maxLength={12000} placeholder={t('objectivePlaceholder')} aria-describedby="task-objective-help" /><small id="task-objective-help">{t('objectiveHelp')}</small></label>
      <label className="field" htmlFor="task-expected"><span>{t('expectedResult')}</span><textarea id="task-expected" value={expectedResult} onChange={(event) => setExpectedResult(event.target.value)} required rows={2} maxLength={4000} placeholder={t('expectedPlaceholder')} /></label>
      {toolsAvailable && <div className="transfer-notice"><label className="checkbox-field"><input type="checkbox" checked={toolMode} disabled={deliverables.length > 0} onChange={(event) => setToolMode(event.target.checked)} aria-describedby="task-tools-help" /><span>{tools('choice')}</span></label><small id="task-tools-help">{tools('help')}</small>{deliverables.length > 0 && <p>{tools('files')}</p>}{toolMode && <p className="quiet-note">{tools('private')}</p>}</div>}
      <fieldset className="task-deliverables-choice" aria-describedby="task-deliverables-help" disabled={toolMode}>
        <legend>{t('deliverables.choose')}</legend>
        {deliverableKinds.map((kind) => <label key={kind} className="checkbox-field" htmlFor={`task-deliverable-${kind}`}><input id={`task-deliverable-${kind}`} type="checkbox" checked={deliverables.includes(kind)} onChange={(event) => setDeliverables((current) => event.target.checked ? [...current, kind] : current.filter((value) => value !== kind))} /><span>{t(`deliverables.kind.${kind}`)}</span></label>)}
        <small id="task-deliverables-help">{t('deliverables.chooseHelp')}</small>
      </fieldset>
      <p className="quiet-note">{t('cost')}</p>
      <div className="task-actions"><button type="button" className="text-button" onClick={onClose}>{t('close')}</button><button type="submit" className="button primary" disabled={!title.trim() || !objective.trim() || !expectedResult.trim()}>{t(busy ? 'sending' : 'delegate')}</button></div>
    </fieldset>
  </form>
}
