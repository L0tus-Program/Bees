import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FolderOpen, LogOut, Menu, Monitor, Plus, Settings, Database, X, MessageSquare } from 'lucide-react'
import { onboarding } from '../../api/onboarding'
import type { AgentSummary, Onboarding } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { AppearanceSelect } from '../../components/AppearanceSelect'
import { useResource } from '../../hooks/useResource'
import { CreateBee } from '../setup/CreateBee'
import { TaskDetail } from '../tasks/TaskDetail'
import { ConversationArtifacts } from '../artifacts/ConversationArtifacts'
import { rememberWorkspace, restoreWorkspace } from '../activity/context'
import { ChatView } from './ChatView'
import { ComputerPanel } from './ComputerPanel'
import { SafetyButton } from './SafetyButton'
import { SettingsPanel } from './SettingsView'
import type { SettingsView } from './settingsViews'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import { Tooltip, TooltipProvider } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

function useWide(query = '(min-width: 1024px)') {
  const [wide, setWide] = useState(() => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const media = window.matchMedia(query)
    const change = () => setWide(media.matches)
    media.addEventListener?.('change', change)
    return () => media.removeEventListener?.('change', change)
  }, [query])
  return wide
}

function BeeList({ data, selected, onSelect, onCreate }: { data: Onboarding; selected: AgentSummary; onSelect: (id: string) => void; onCreate: () => void }) {
  const { t } = useTranslation('workspace')
  return <div className="flex min-h-0 flex-1 flex-col gap-2">
    <Button variant="outline" className="justify-start" onClick={onCreate}><Plus />{t('newBee')}</Button>
    <h2 className="px-2 pt-3 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('bees')}</h2>
    <ul className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
      {data.agents.map((agent) => <li key={agent.id}><button type="button" aria-current={agent.id === selected.id ? 'page' : undefined} onClick={() => onSelect(agent.id)}
        className={cn('flex w-full cursor-pointer items-center gap-3 rounded-lg border-0 px-2 py-2 text-left text-foreground hover:bg-muted', agent.id === selected.id ? 'bg-muted' : 'bg-transparent')}>
        <img src="/bee.svg" alt="" width="28" height="28" className="size-7 shrink-0" />
        <span className="min-w-0"><span className="block truncate text-sm font-medium">{agent.name}</span><span className="block truncate text-xs text-muted-foreground">{agent.purpose}</span></span>
      </button></li>)}
    </ul>
  </div>
}

/** Área autenticada no estilo assistente: abelhas à esquerda, conversa ao centro, computador à direita. */
export function Workspace({ onSignOut, onData, signingOut }: { onSignOut: () => void; onData: () => void; signingOut: boolean }) {
  const { t, i18n } = useTranslation('workspace')
  const { t: product } = useTranslation('product')
  const { resource, reload } = useResource(onboarding)
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(() => restoreWorkspace().selectedId)
  const [settings, setSettings] = useState<SettingsView | null>(null)
  const [menu, setMenu] = useState(false)
  const [computer, setComputer] = useState(false)
  const [files, setFiles] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const wide = useWide()
  useEffect(() => { rememberWorkspace(selectedId, 'conversation') }, [selectedId])
  const openComputer = useCallback(() => setComputer(true), [])

  if (resource.state === 'loading') return <div className="ui grid h-dvh place-items-center bg-background"><LoadingNotice /></div>
  if (resource.state === 'error') return <div className="ui grid h-dvh place-items-center bg-background"><div className="legacy-scope surface resource-error"><ErrorNotice error={resource.error} /><button className="button secondary" onClick={reload} type="button">{product('retry')}</button></div></div>
  const data = resource.data
  if (creating || data.agents.length === 0) {
    return <div className="ui min-h-dvh bg-background"><div className="legacy-scope mx-auto max-w-5xl p-4 md:p-8"><CreateBee vault={data.vault} onCreated={(agent) => { setSelectedId(agent.id); setCreating(false); reload() }} onCancel={data.agents.length ? () => setCreating(false) : undefined} onReconcile={() => { setCreating(false); reload() }} /></div></div>
  }
  const selected = data.agents.find((agent) => agent.id === selectedId) ?? data.agents[0]
  const select = (id: string) => { setSelectedId(id); setSettings(null); setMenu(false); setComputer(false) }

  const sidebar = <div className="flex h-full flex-col gap-3 p-3">
    <div className="flex items-center gap-2 px-2 py-1"><img src="/bee.svg" alt="" width="28" height="28" /><span className="text-lg font-bold tracking-tight">bees<span className="text-primary">.</span></span></div>
    <BeeList data={data} selected={selected} onSelect={select} onCreate={() => { setCreating(true); setMenu(false) }} />
    <div className="flex flex-col gap-1 border-t pt-3">
      <SafetyButton />
      <Button variant="ghost" size="sm" className="justify-start" onClick={() => { setSettings(settings ? null : 'profile'); setMenu(false) }}><Settings />{t('settings')}</Button>
      <Button variant="ghost" size="sm" className="justify-start" onClick={onData}><Database />{t('data')}</Button>
      <div className="legacy-scope flex flex-wrap items-center gap-2 px-2 py-1 text-sm">
        <AppearanceSelect />
        <label className="language-select"><span className="sr-only">{product('language')}</span><select value={i18n.resolvedLanguage ?? 'pt-BR'} onChange={(event) => { void i18n.changeLanguage(event.target.value) }}><option value="pt-BR">Português</option><option value="en">English</option><option value="es">Español</option></select></label>
      </div>
      <Button variant="ghost" size="sm" className="justify-start" disabled={signingOut} onClick={onSignOut}><LogOut />{t('signOut')}</Button>
    </div>
  </div>

  const conversation = selected.provider_config && selected.conversation_id
  return <TooltipProvider>
    <div className="ui flex h-dvh overflow-hidden bg-background font-sans text-foreground">
      <aside className="hidden w-72 shrink-0 border-r bg-muted/30 lg:block" aria-label={t('bees')}>{sidebar}</aside>
      {!wide && <Sheet open={menu} onOpenChange={setMenu} title="bees." closeLabel={t('closePanel')} side="left" className="max-w-xs [&>div:last-child]:p-0">{sidebar}</Sheet>}
      <main id="main" tabIndex={-1} className="flex min-w-0 flex-1 flex-col outline-none">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-3">
          <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label={t('menu')} onClick={() => setMenu(true)}><Menu /></Button>
          <img src="/bee.svg" alt="" width="24" height="24" className="hidden size-6 sm:block" />
          <div className="min-w-0 flex-1"><h1 className="truncate text-base font-semibold">{selected.name}</h1>
            {selected.provider_config && <p className="truncate text-xs text-muted-foreground">{selected.provider_config.model}</p>}</div>
          {selected.status !== 'active' && <Badge variant="warning">{selected.status}</Badge>}
          {settings ? <Button variant="outline" size="sm" onClick={() => setSettings(null)}><MessageSquare />{t('backToChat')}</Button> : <>
            <Tooltip label={t('files')}><Button variant="ghost" size="icon-sm" aria-label={t('files')} aria-pressed={files} onClick={() => setFiles(true)}><FolderOpen /></Button></Tooltip>
            <Tooltip label={t('computer')}><Button variant={computer ? 'secondary' : 'ghost'} size="icon-sm" aria-label={t('computer')} aria-pressed={computer} onClick={() => setComputer((value) => !value)}><Monitor /></Button></Tooltip>
            <Tooltip label={t('settings')}><Button variant="ghost" size="icon-sm" aria-label={t('settings')} onClick={() => setSettings('profile')}><Settings /></Button></Tooltip>
          </>}
        </header>
        {settings ? <SettingsPanel key={selected.id} agent={selected} data={data} initial={settings} onReload={reload} />
          : conversation ? <ChatView key={`${selected.id}:${selected.conversation_id}`} agent={{ ...selected, conversation_id: selected.conversation_id! }} onTaskDetails={setTaskId} onComputerActivity={openComputer} />
            : <div className="grid flex-1 place-items-center p-6 text-center"><div className="flex flex-col items-center gap-3"><p className="text-muted-foreground">{t('noModel')}</p><Button onClick={() => setSettings('model')}>{t('configureModel')}</Button></div></div>}
      </main>
      {!settings && computer && wide && <aside className="flex w-[min(44vw,640px)] shrink-0 flex-col border-l p-3" aria-label={t('computer')}>
        <div className="mb-2 flex justify-end"><Button variant="ghost" size="icon-sm" aria-label={t('closePanel')} onClick={() => setComputer(false)}><X /></Button></div>
        <ComputerPanel key={selected.id} agentId={selected.id} name={selected.name} />
      </aside>}
      {!wide && <Sheet open={!settings && computer} onOpenChange={setComputer} title={t('computer')} closeLabel={t('closePanel')} className="max-w-full"><ComputerPanel key={selected.id} agentId={selected.id} name={selected.name} /></Sheet>}
      <Sheet open={files} onOpenChange={setFiles} title={t('files')} closeLabel={t('closePanel')}>
        {conversation && <div className="legacy-scope"><ConversationArtifacts agentId={selected.id} conversationId={selected.conversation_id!} selected={[]} onSelected={() => undefined} onReady={() => undefined} blocked={false} /></div>}
      </Sheet>
      <Sheet open={taskId !== null} onOpenChange={(open) => { if (!open) setTaskId(null) }} title={t('activity')} closeLabel={t('closePanel')} className="max-w-2xl">
        {taskId && <div className="legacy-scope"><TaskDetail key={taskId} agent={selected} taskId={taskId} onRefresh={() => undefined} onClose={() => setTaskId(null)} /></div>}
      </Sheet>
    </div>
  </TooltipProvider>
}
