import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentSummary } from '../../api/onboarding'
import type { ActivityScope } from '../../api/activity'
import { ErrorNotice } from '../../components/Feedback'
import { BudgetPanel } from '../budgets/BudgetPanel'
import { TaskDetail } from '../tasks/TaskDetail'
import { ActivityReader } from './state'
import type { ActivityView } from './state'
import { ActivitySummary, ActivityTime, ActivityTimeline } from './ActivityTimeline'
import { rememberActivityContext, restoreActivityContext } from './context'
import './activity.css'

export function ActivityPanel({ agent, onPreferences }: { agent: AgentSummary; onPreferences: () => void }) {
  return <ActivityWorkspace key={agent.id} agent={agent} onPreferences={onPreferences} />
}
function ActivityWorkspace({ agent, onPreferences }: { agent: AgentSummary; onPreferences: () => void }) {
  const { t } = useTranslation('activity')
  const [context] = useState(() => restoreActivityContext(agent.id))
  const [days, setDays] = useState(context.days)
  const [scope, setScope] = useState<ActivityScope>(context.scope)
  const [taskId, setTaskId] = useState<string | null>(context.open_task_id)
  const [refreshToken, setRefreshToken] = useState(0)
  useEffect(() => { rememberActivityContext(agent.id, { days, scope, open_task_id: taskId }) }, [agent.id, days, scope, taskId])
  return <>
    <BudgetPanel agent={agent} />
    <section className="surface activity-panel" aria-labelledby="activity-heading">
      <div className="form-heading"><h2 id="activity-heading">{t('title')}</h2><button className="text-button" type="button" onClick={onPreferences}>{t('preferences')}</button></div>
      <p className="form-introduction">{t('description')}</p>
      <div className="activity-filters"><label className="field"><span>{t('days')}</span><select value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>{t('day')}</option><option value={7}>{t('week')}</option><option value={30}>{t('month')}</option></select></label>
        <div>{scope.task_id ? <><p className="quiet-note">{t('task')}: <code>{scope.task_id}</code>{scope.run_id && <><br />{t('run')}: <code>{scope.run_id}</code></>}</p><button className="text-button" type="button" onClick={() => setScope({ task_id: null, run_id: null })}>{t('clear')}</button></> : <p className="quiet-note">{t('all')}</p>}</div>
      </div>
      <ActivityResource key={`${days}/${scope.task_id}/${scope.run_id}`} agentId={agent.id} days={days} scope={scope} refreshToken={refreshToken} onOpen={setTaskId} onScope={(task, run) => { setScope({ task_id: task, run_id: run ?? null }) }} />
    </section>
    {taskId && <div className="surface activity-task-detail"><TaskDetail key={`${agent.id}/${taskId}`} agent={agent} taskId={taskId} onClose={() => { setTaskId(null); setRefreshToken((value) => value + 1) }} onRefresh={() => setRefreshToken((value) => value + 1)} /></div>}
  </>
}
function ActivityResource({ agentId, days, scope, refreshToken, onOpen, onScope }: { agentId: string; days: number; scope: ActivityScope; refreshToken: number; onOpen: (taskId: string) => void; onScope: (taskId: string, runId?: string) => void }) {
  const { t } = useTranslation('activity')
  const [view, setView] = useState<ActivityView>({ page: null, loading: true, error: null })
  const reader = useRef<ActivityReader | null>(null)
  useEffect(() => {
    const resource = new ActivityReader(agentId, days, scope, setView); reader.current = resource; void resource.load()
    return () => { resource.stop(); reader.current = null }
  }, [agentId, days, scope])
  const observedRefresh = useRef(refreshToken)
  useEffect(() => { if (observedRefresh.current !== refreshToken) { observedRefresh.current = refreshToken; void reader.current?.load() } }, [refreshToken])
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  return <div aria-busy={view.loading}>
    <button className="text-button" type="button" disabled={view.loading} onClick={() => { void reader.current?.load() }}>{t('refresh')}</button>
    {view.loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {view.error && <><ErrorNotice error={view.error} /><p className="quiet-note" role="status">{t(view.error.status === 409 ? 'changed' : 'stale')}</p></>}
    {view.page && <>
      <p className="activity-interval">{t('interval')}: <ActivityTime value={view.page.interval.since} /> — <ActivityTime value={view.page.interval.until} /><br />{t('timezone', { zone: timezone })}<br />{t('observed')}: <ActivityTime value={view.page.snapshot.observed_at} /></p>
      <ActivitySummary page={view.page} />
      <p className="quiet-note">{t('historyHelp')}</p>
      {view.page.items.length === 0 && !view.loading && !view.error && <p className="quiet-note">{t('empty')}</p>}
      <ActivityTimeline items={view.page.items} disabled={view.loading || !!view.error} onOpen={onOpen} onScope={onScope} />
      {view.page.next_before !== null && <button className="button secondary" type="button" disabled={view.loading || !!view.error} onClick={() => { void reader.current?.load(true) }}>{t('more')}</button>}
    </>}
  </div>
}
