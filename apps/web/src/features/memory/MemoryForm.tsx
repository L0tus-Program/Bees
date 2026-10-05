import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createMemory, updateMemory } from '../../api/bee'
import type { MemoryCollection, MemoryRecord } from '../../api/bee'
import { ErrorNotice } from '../../components/Feedback'
import { observedAt, toDateTimeLocal } from './format'

export function MemoryForm({ agentId, memory, tasks, onSaved, onCancel, onReload }: { agentId: string; memory: MemoryRecord | null; tasks: MemoryCollection['tasks']; onSaved: () => void; onCancel: () => void; onReload: () => void }) {
  const { t } = useTranslation('bee')
  const { t: product } = useTranslation('product')
  const [scope, setScope] = useState<MemoryRecord['scope']>(memory?.scope ?? 'agent')
  const [taskId, setTaskId] = useState(memory?.task_id ?? tasks[0]?.id ?? '')
  const [kind, setKind] = useState<MemoryRecord['kind']>(memory?.kind ?? 'preference')
  const [content, setContent] = useState(memory?.content ?? '')
  const [source, setSource] = useState(memory?.source_ref ?? '')
  const [observed, setObserved] = useState(toDateTimeLocal(memory?.observed_at ?? null))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  useEffect(() => { headingRef.current?.focus(); return () => controller.current?.abort() }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(null); controller.current = new AbortController()
    try {
      const unchangedObserved = memory && observed === toDateTimeLocal(memory.observed_at)
      const data = { content: content.trim(), kind, source_ref: source.trim() || null, observed_at: unchangedObserved ? memory.observed_at : observedAt(observed) }
      if (memory) await updateMemory(agentId, memory.id, memory.revision, data, controller.current.signal)
      else await createMemory(agentId, { ...data, scope, ...(scope === 'task' ? { task_id: taskId } : {}) }, controller.current.signal)
      if (!controller.current.signal.aborted) onSaved()
    } catch (failure) { if (!controller.current.signal.aborted) setError(asApiError(failure)) }
    finally { setBusy(false) }
  }

  return <form className="surface memory-form" onSubmit={(event) => { void submit(event) }}>
    <div className="form-heading"><h2 ref={headingRef} tabIndex={-1}>{t(memory ? 'editMemory' : 'newMemory')}</h2><button className="text-button" type="button" onClick={onCancel} disabled={busy}>{product('back')}</button></div>
    <p className="form-introduction">{t('memoryFormDescription')}</p>
    {error && <><ErrorNotice error={error} /><button className="text-button" type="button" onClick={onReload}>{t('reloadCurrent')}</button></>}
    <fieldset disabled={busy}>
      <div className="form-row">
        <label className="field" htmlFor="memory-scope"><span>{t('memoryScope')}</span><select id="memory-scope" value={scope} disabled={!!memory} onChange={(event) => setScope(event.target.value as MemoryRecord['scope'])}><option value="user">{t('scope_user')}</option><option value="agent">{t('scope_agent')}</option>{(tasks.length > 0 || memory?.scope === 'task') && <option value="task">{t('scope_task')}</option>}</select><small>{t(`scopeHelp_${scope}`)}</small></label>
        <label className="field" htmlFor="memory-kind"><span>{t('memoryKind')}</span><select id="memory-kind" value={kind} onChange={(event) => setKind(event.target.value as MemoryRecord['kind'])}><option value="preference">{t('kind_preference')}</option><option value="fact">{t('kind_fact')}</option></select><small>{t(kind === 'fact' ? 'factHelp' : 'preferenceHelp')}</small></label>
      </div>
      {scope === 'task' && <label className="field" htmlFor="memory-task"><span>{t('task')}</span>{memory ? <input id="memory-task" value={tasks.find((task) => task.id === taskId)?.title ?? taskId} readOnly /> : <select id="memory-task" value={taskId} required onChange={(event) => setTaskId(event.target.value)}>{tasks.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}</select>}<small>{t('taskRecordsOnly')}</small></label>}
      <label className="field" htmlFor="memory-content"><span>{t('memoryContent')}</span><textarea id="memory-content" value={content} onChange={(event) => setContent(event.target.value)} rows={4} maxLength={8192} required placeholder={t('memoryPlaceholder')} /></label>
      <div className="form-row">
        <label className="field" htmlFor="memory-source"><span>{t('memorySource')}{kind !== 'fact' && <span className="optional">{product('optional')}</span>}</span><input id="memory-source" value={source} onChange={(event) => setSource(event.target.value)} required={kind === 'fact'} maxLength={2048} placeholder={t('sourcePlaceholder')} /><small>{t('sourceHelp')}</small></label>
        <label className="field" htmlFor="memory-observed"><span>{t('observedAt')}{kind !== 'fact' && <span className="optional">{product('optional')}</span>}</span><input id="memory-observed" type="datetime-local" value={observed} onChange={(event) => setObserved(event.target.value)} required={kind === 'fact'} /><small>{t('timezone', { zone: Intl.DateTimeFormat().resolvedOptions().timeZone })}</small></label>
      </div>
      <button className="button primary" type="submit">{busy ? product('working') : t('saveMemory')}</button>
    </fieldset>
  </form>
}
