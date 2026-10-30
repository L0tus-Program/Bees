import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Brain, Check, ChevronDown, Clock, FileText, Globe, Laptop, Loader2, Monitor, Plug, Users, Wrench, X } from 'lucide-react'
import type { TaskRecord } from '../../api/tasks'
import { getArtifacts } from '../artifacts/api'
import type { ArtifactRecord } from '../artifacts/api'
import { AgentApprovals } from '../agent/AgentApprovals'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Step = NonNullable<TaskRecord['agent_steps']>[number]
const LIVE = new Set(['queued', 'running'])

function StepIcon({ tool }: { tool: string }) {
  const name = tool.toLowerCase()
  const Icon = name.startsWith('web') ? Globe : name.startsWith('memory') ? Brain : name.includes('publish') ? FileText
    : name.startsWith('computer') ? Monitor : name.startsWith('personal') ? Laptop : name.startsWith('mcp') ? Plug : name.startsWith('bees') ? Users : Wrench
  return <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
}

function size(bytes: number, language: string) {
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes; let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: unit ? 1 : 0 }).format(value)} ${units[unit]}`
}

/** Arquivos publicados pela tarefa, como anexos da resposta. */
export function TurnFiles({ task }: { task: TaskRecord }) {
  const { t, i18n } = useTranslation('workspace')
  const [files, setFiles] = useState<ArtifactRecord[]>([])
  const done = task.status === 'completed' || task.status === 'paused'
  useEffect(() => {
    if (!done) return
    const controller = new AbortController()
    getArtifacts(task.agent_id, task.id, 0, controller.signal).then((page) => setFiles(page.items.filter((item) => item.status === 'ready')), () => setFiles([]))
    return () => controller.abort()
  }, [done, task.agent_id, task.id])
  if (!files.length) return null
  return <ul className="mt-2 flex flex-wrap gap-2">
    {files.map((file) => <li key={file.id}>
      <a className="flex max-w-xs items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm text-foreground no-underline shadow-xs hover:bg-muted" href={`/api/v1/agents/${encodeURIComponent(task.agent_id)}/artifacts/${encodeURIComponent(file.id)}/download`} download={file.name} aria-label={t('download', { name: file.name })}>
        <FileText className="size-4 shrink-0 text-primary" aria-hidden="true" />
        <span className="min-w-0 truncate font-medium">{file.name}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{size(file.size_bytes, i18n.resolvedLanguage ?? 'pt-BR')}</span>
      </a>
    </li>)}
  </ul>
}

/** Turno da abelha no modo agente: passos ao vivo, decisões, estado e arquivos — sem jargão de tarefa. */
export function AgentTurn({ task, hasReply, showFiles = true, onChanged, onDetails }: { task: TaskRecord; hasReply: boolean; showFiles?: boolean; onChanged: () => void; onDetails: () => void }) {
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
      {task.status !== 'waiting_approval' && <Button variant="link" size="sm" className="h-auto px-0" onClick={onDetails}>{t('details')}</Button>}</div>}
    {task.status === 'waiting_approval' && <div className="legacy-scope"><AgentApprovals agentId={task.agent_id} taskId={task.id} onChanged={onChanged} refreshKey={task.revision} /></div>}
    {showFiles && (hasReply || !live) && <TurnFiles task={task} />}
  </div>
}
