import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createTask } from '../../api/tasks'
import type { TaskRecord } from '../../api/tasks'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { commandIdentity } from './state'

export function TaskForm({ agent, onCreated, onClose, onRefresh }: { agent: AgentSummary; onCreated: (task: TaskRecord) => void; onClose: () => void; onRefresh: () => void }) {
  const { t } = useTranslation('tasks')
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const [expectedResult, setExpectedResult] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [identity] = useState(commandIdentity)
  const controller = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { input.current?.focus(); return () => controller.current?.abort() }, [])
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !title.trim() || !objective.trim() || !expectedResult.trim()) return
    setBusy(true); setError(null)
    const operation = new AbortController(); controller.current = operation
    const body = { title: title.trim(), objective: objective.trim(), expected_result: expectedResult.trim(), ...(agent.conversation_id ? { conversation_id: agent.conversation_id } : {}) }
    try {
      const task = await createTask(agent.id, { ...body, client_request_id: identity.forPayload(body) }, operation.signal)
      if (!operation.signal.aborted) { identity.reset(); onCreated(task) }
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); onRefresh() } }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  return <form className="task-form" onSubmit={(event) => { void submit(event) }} aria-label={t('delegate')}>
    {error && <><ErrorNotice error={error} /><p className="quiet-note">{t('uncertainMutation')}</p><button type="button" className="text-button" onClick={onRefresh}>{t('refresh')}</button></>}
    <fieldset disabled={busy}>
      <label className="field" htmlFor="task-title"><span>{t('title')}</span><input id="task-title" ref={input} value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={200} /></label>
      <label className="field" htmlFor="task-objective"><span>{t('objective')}</span><textarea id="task-objective" value={objective} onChange={(event) => setObjective(event.target.value)} required rows={3} maxLength={12000} placeholder={t('objectivePlaceholder')} /></label>
      <label className="field" htmlFor="task-expected"><span>{t('expectedResult')}</span><textarea id="task-expected" value={expectedResult} onChange={(event) => setExpectedResult(event.target.value)} required rows={2} maxLength={4000} placeholder={t('expectedPlaceholder')} /></label>
      <p className="quiet-note">{t('cost')}</p>
      <div className="task-actions"><button type="button" className="text-button" onClick={onClose}>{t('close')}</button><button type="submit" className="button primary" disabled={!title.trim() || !objective.trim() || !expectedResult.trim()}>{t(busy ? 'sending' : 'delegate')}</button></div>
    </fieldset>
  </form>
}
