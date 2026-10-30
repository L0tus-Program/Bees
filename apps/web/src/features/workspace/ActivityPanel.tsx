import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check, ChevronRight, Clock, Loader2, Pause, X } from 'lucide-react'
import type { TaskRecord, TaskStatus } from '../../api/tasks'
import { ErrorNotice } from '../../components/Feedback'
import { cn } from '@/lib/utils'
import { ACTIVE, needsYou } from './useActivity'
import type { useActivity } from './useActivity'

function StatusIcon({ status }: { status: TaskStatus }) {
  const className = 'size-4 shrink-0'
  if (status === 'queued' || status === 'running') return <Loader2 className={cn(className, 'animate-spin text-primary')} aria-hidden="true" />
  if (status === 'waiting_approval') return <AlertTriangle className={cn(className, 'text-accent-foreground')} aria-hidden="true" />
  if (status === 'waiting_resource') return <Clock className={cn(className, 'text-muted-foreground')} aria-hidden="true" />
  if (status === 'paused') return <Pause className={cn(className, 'text-accent-foreground')} aria-hidden="true" />
  if (status === 'completed') return <Check className={cn(className, 'text-muted-foreground')} aria-hidden="true" />
  return <X className={cn(className, status === 'failed' ? 'text-destructive' : 'text-muted-foreground')} aria-hidden="true" />
}

function ago(value: string, language: string) {
  const seconds = Math.round((Date.parse(value) - Date.now()) / 1000)
  const format = new Intl.RelativeTimeFormat(language, { numeric: 'auto', style: 'short' })
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [['second', 60], ['minute', 60], ['hour', 24], ['day', 7], ['week', 5], ['month', 12]]
  let amount = seconds
  for (const [unit, size] of steps) {
    if (Math.abs(amount) < size) return format.format(Math.min(amount, 0), unit)
    amount = Math.trunc(amount / size)
  }
  return format.format(Math.min(amount, 0), 'year')
}

function TaskRow({ task, onOpen }: { task: TaskRecord; onOpen: (id: string) => void }) {
  const { t, i18n } = useTranslation('workspace')
  return <li>
    <button type="button" onClick={() => onOpen(task.id)} className="group flex w-full cursor-pointer items-center gap-3 rounded-lg border-0 bg-transparent px-2 py-2 text-left text-foreground hover:bg-muted">
      <StatusIcon status={task.status} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{task.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{t(`status.${task.status}`)} · {ago(task.updated_at, i18n.resolvedLanguage ?? 'pt-BR')}</span>
      </span>
      <ChevronRight className="size-4 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100" aria-hidden="true" />
    </button>
  </li>
}

function Group({ title, tasks, onOpen, limit }: { title: string; tasks: TaskRecord[]; onOpen: (id: string) => void; limit?: number }) {
  const { t } = useTranslation('workspace')
  const [all, setAll] = useState(false)
  if (!tasks.length) return null
  const shown = limit && !all ? tasks.slice(0, limit) : tasks
  return <section className="flex flex-col gap-1">
    <h3 className="px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title} <span className="font-normal">({tasks.length})</span></h3>
    <ul className="flex flex-col">{shown.map((task) => <TaskRow key={task.id} task={task} onOpen={onOpen} />)}</ul>
    {shown.length < tasks.length && <button type="button" className="cursor-pointer self-start rounded-md border-0 bg-transparent px-2 py-1 text-xs text-primary hover:underline" onClick={() => setAll(true)}>{t('showAll', { count: tasks.length })}</button>}
  </section>
}

/** Lista compacta, agrupada: o que precisa de você, o que está andando e o que já terminou. */
export function ActivityPanel({ activity, onOpen }: { activity: ReturnType<typeof useActivity>; onOpen: (id: string) => void }) {
  const { t } = useTranslation('workspace')
  const { tasks } = activity
  if (activity.loading && !activity.data) return <p className="flex items-center gap-2 p-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden="true" />{t('thinking')}</p>
  const attention = tasks.filter(needsYou)
  const running = tasks.filter((task) => ACTIVE.has(task.status) && !needsYou(task))
  const recent = tasks.filter((task) => !ACTIVE.has(task.status) && !needsYou(task))
  return <div className="flex flex-col gap-4">
    {activity.error && <div className="legacy-scope"><ErrorNotice error={activity.error} /></div>}
    {!tasks.length && !activity.error && <p className="px-2 py-6 text-center text-sm text-muted-foreground">{t('activityEmpty')}</p>}
    <Group title={t('needsYou')} tasks={attention} onOpen={onOpen} />
    <Group title={t('inProgress')} tasks={running} onOpen={onOpen} />
    <Group title={t('recent')} tasks={recent} onOpen={onOpen} limit={8} />
  </div>
}
