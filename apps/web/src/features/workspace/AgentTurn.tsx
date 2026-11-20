import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Brain, Check, ChevronDown, Clock, FileText, Globe, Laptop, Loader2, Monitor, Plug, Users, Wrench, X } from 'lucide-react'
import type { TaskRecord } from '../../api/tasks'
import { ArtifactReader } from '../artifacts/reader'
import type { ArtifactView } from '../artifacts/reader'
import { TaskFailure } from '../tasks/TaskFailure'
import { AgentApprovals } from '../agent/AgentApprovals'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { TurnFileDownloads } from './TurnFileDownloads'

type Step = NonNullable<TaskRecord['agent_steps']>[number]
const LIVE = new Set(['queued', 'running'])

function StepIcon({ tool }: { tool: string }) {
  const name = tool.toLowerCase()
  const Icon = name.startsWith('web') ? Globe : name.startsWith('memory') ? Brain : name.includes('publish') ? FileText
    : name.startsWith('computer') ? Monitor : name.startsWith('personal') ? Laptop : name.startsWith('mcp') ? Plug : name.startsWith('bees') ? Users : Wrench
  return <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
}

/** Arquivos publicados pela tarefa, como anexos da resposta. */
export function TurnFiles({ task }: { task: TaskRecord }) {
  if (task.status !== 'completed' && task.status !== 'paused') return null
  return <TurnFilesPage key={`${task.agent_id}:${task.id}:${task.revision}:${task.status}`} agentId={task.agent_id} taskId={task.id} />
}

function TurnFilesPage({ agentId, taskId }: { agentId: string; taskId: string }) {
  const { t } = useTranslation('workspace')
  const [view, setView] = useState<ArtifactView>({ page: null, loading: true, error: null })
  const reader = useRef<ArtifactReader | null>(null)
  useEffect(() => {
    const observer = new ArtifactReader(agentId, taskId, setView); reader.current = observer
    void observer.load()
    return () => { observer.stop(); reader.current = null }
  }, [agentId, taskId])
  function loadMore() {
    const observer = reader.current
    // Reader state changes synchronously, before React disables the button.
    if (!observer || observer.state.loading) return
    void observer.load(observer.state.page?.next_offset ?? 0)
  }
  const files = view.page?.items.filter((item) => item.status === 'ready') ?? []
  return <div aria-busy={view.loading}>
    {files.length > 0 && <TurnFileDownloads agentId={agentId} taskId={taskId} files={files} />}
    {view.loading && <p className="text-xs text-muted-foreground" role="status">{t('loadingFiles')}</p>}
    {view.error && <div className="flex flex-col items-start gap-2"><p className="text-sm text-destructive" role="alert">{t('filesLoadError')}</p><Button variant="outline" size="sm" onClick={loadMore}>{t('retry')}</Button></div>}
    {view.page?.has_more && !view.error && <Button variant="outline" size="sm" disabled={view.loading} onClick={loadMore}>{t('moreFiles')}</Button>}
  </div>
}

/** Turno da abelha no modo agente: passos ao vivo, decisões, estado e arquivos — sem jargão de tarefa. */
export function AgentTurn({ task, hasReply, showFiles = true, onChanged, onDetails }: { task: TaskRecord; hasReply: boolean; showFiles?: boolean; onChanged: () => void; onDetails: (trigger: HTMLButtonElement) => void }) {
  const { t, i18n } = useTranslation('workspace')
  const { t: agent } = useTranslation('agent')
  const steps: Step[] = task.agent_steps ?? []
  const live = LIVE.has(task.status)
  const [open, setOpen] = useState(false)
  const expanded = live || open
  const label = (step: Step) => agent(`functions.${step.tool}`, { defaultValue: step.tool.replace(/_/g, ' ') })
  const reason = (step: Step) => step.code ? (i18n.exists(`stepCode.${step.code}`, { ns: 'agent' }) ? agent(`stepCode.${step.code}`) : agent('stepCodeOther', { code: step.code })) : null
  const status = task.status === 'waiting_approval' ? { text: t('needsDecision'), variant: 'warning' as const, Icon: AlertTriangle }
    : task.status === 'waiting_resource' ? { text: t('waitingResource'), variant: 'secondary' as const, Icon: Clock }
    : task.status === 'paused' ? { text: t('paused'), variant: 'warning' as const, Icon: AlertTriangle }
    : task.status === 'failed' ? { text: t('failed'), variant: 'destructive' as const, Icon: X }
    : task.status === 'cancelled' ? { text: t('cancelled'), variant: 'secondary' as const, Icon: X } : null
  return <div className="flex flex-col gap-2">
    {live && !steps.length && <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Loader2 className="size-4 animate-spin" aria-hidden="true" />{t('thinking')}</p>}
    {steps.length > 0 && <div className="rounded-lg border bg-muted/40 text-sm">
      <button type="button" className="flex w-full cursor-pointer items-center gap-2 rounded-lg border-0 bg-transparent px-3 py-2 text-left text-muted-foreground hover:text-foreground" aria-expanded={expanded} onClick={() => setOpen((value) => !value)}>
        {live ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Check className="size-4" aria-hidden="true" />}
        <span className="flex-1">{live ? t('working') : t('usedTools', { count: steps.length })}</span>
        {!live && <ChevronDown className={cn('size-4 transition-transform', open && 'rotate-180')} aria-hidden="true" />}
      </button>
      {expanded && <ol className="flex flex-col gap-1 px-3 pb-2" aria-live={live ? 'polite' : undefined}>
        {steps.slice(-12).map((step, index) => <li key={`${step.at}:${index}`} className="flex items-start gap-2 text-muted-foreground">
          <StepIcon tool={step.tool} />
          <span className="min-w-0"><span className="text-foreground">{label(step)}</span>{step.status && step.status !== 'confirmed' && <span> · {agent(`stepStatus.${step.status}`, { defaultValue: step.status })}{reason(step) ? ` · ${reason(step)}` : ''}</span>}</span>
        </li>)}
      </ol>}
    </div>}
    {status && <div className="flex flex-wrap items-center gap-2"><Badge variant={status.variant}><status.Icon aria-hidden="true" />{status.text}</Badge>
      {task.status !== 'waiting_approval' && <Button variant="link" size="sm" className="h-auto px-0" aria-haspopup="dialog" onClick={(event) => onDetails(event.currentTarget)}>{t('details')}</Button>}</div>}
    <TaskFailure task={task} warnUnknown />
    {task.status === 'waiting_approval' && <div className="legacy-scope"><AgentApprovals agentId={task.agent_id} taskId={task.id} onChanged={onChanged} refreshKey={task.revision} /></div>}
    {showFiles && (hasReply || !live) && <TurnFiles task={task} />}
  </div>
}
