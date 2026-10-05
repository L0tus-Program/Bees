import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getTasks } from '../../api/tasks'
import type { TaskCollection } from '../../api/tasks'
import type { AgentSummary } from '../../api/onboarding'
import { TaskForm } from './TaskForm'
import { TaskDetail } from './TaskDetail'
import { TaskTime } from './TaskTime'
import { mergeTaskSnapshot } from './state'
import { useTaskPolling } from './useTaskPolling'

function mergeCollection(incoming: TaskCollection, previous: TaskCollection) { return { ...incoming, tasks: mergeTaskSnapshot(incoming.tasks, previous.tasks) } }

export function TaskPanel({ agent }: { agent: AgentSummary }) {
  const { t } = useTranslation('tasks')
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const load = useCallback((signal: AbortSignal) => getTasks(agent.id, signal), [agent.id])
  const monitor = useTaskPolling(load, mergeCollection)
  return <section className="surface task-panel" aria-labelledby="tasks-heading">
    <div className="form-heading"><div><p className="section-label">{t('background')}</p><h2 id="tasks-heading">{t('heading')}</h2></div><button type="button" className="button secondary" disabled={creating || agent.status !== 'active' || !agent.provider_config} onClick={() => setCreating(true)}>{t('delegate')}</button></div>
    <p className="form-introduction">{t('scope')}</p>
    {monitor.data?.worker?.available === false && <p className="transfer-notice" role="status">{t('workerUnavailable')}</p>}
    {creating && <TaskForm agent={agent} onClose={() => setCreating(false)} onRefresh={monitor.refresh} onCreated={(task) => { setCreating(false); setSelectedId(task.id); monitor.refresh() }} />}
    {monitor.loading && <p className="quiet-note" role="status">{t('loading')}</p>}
    {monitor.error && <p className="form-error" role="status">{t('monitorFailure')}</p>}
    <div className="task-list-toolbar"><small>{t('observation')}</small><button type="button" className="text-button" onClick={monitor.refresh}>{t('refresh')}</button></div>
    {monitor.data && <>
      {!monitor.data.tasks.length && <p className="task-empty">{t('empty')}</p>}
      {monitor.data.has_more && <p className="quiet-note">{t('limited')}</p>}
      <div className="task-list">{monitor.data.tasks.map((task) => <button type="button" key={task.id} className={`task-item ${selectedId === task.id ? 'selected' : ''}`} aria-pressed={selectedId === task.id} onClick={() => setSelectedId(task.id)}><strong>{task.title}</strong><span>{t(`status.${task.status}`)}{task.control_requested && ` · ${t(`requested.${task.control_requested}`)}`}</span><small><TaskTime value={task.updated_at} /></small></button>)}</div>
    </>}
    {selectedId && <TaskDetail key={`${agent.id}:${selectedId}`} agent={agent} taskId={selectedId} onRefresh={monitor.refresh} onClose={() => setSelectedId(null)} />}
  </section>
}
