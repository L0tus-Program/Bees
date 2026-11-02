import { lazy, Suspense, useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentSummary, Onboarding } from '../../api/onboarding'
import { LoadingNotice } from '../../components/Feedback'
import { ProfileEditor } from '../bee/ProfileEditor'
import { AgentActivation } from '../bee/AgentActivation'
import { ModelEditor } from '../bee/ModelEditor'
import { MemoryPanel } from '../memory/MemoryPanel'
import { TaskPanel } from '../tasks/TaskPanel'
import { PolicyPanel } from '../policies/PolicyPanel'
import { ActivityPanel } from '../activity/ActivityPanel'
import { AttentionPanel } from '../attention/AttentionPanel'
import { RecentAgentDecisions } from '../agent/AgentApprovals'
import { CheckCircle2 } from 'lucide-react'
import { SettingsNavigation } from './SettingsNavigation'
import type { SettingsView } from './settingsViews'
import './settings.css'

const RoutinesPanel = lazy(async () => ({ default: (await import('../routines/RoutinesPanel')).RoutinesPanel }))
const DeliveryHistoryPanel = lazy(async () => ({ default: (await import('../deliveries/DeliveryHistoryPanel')).DeliveryHistoryPanel }))
const SkillsPanel = lazy(async () => ({ default: (await import('../skills/SkillsPanel')).SkillsPanel }))
const ComputerWorkspace = lazy(async () => ({ default: (await import('../computer/ComputerWorkspace')).ComputerWorkspace }))
const EnvironmentPanel = lazy(async () => ({ default: (await import('../environments/EnvironmentPanel')).EnvironmentPanel }))
const ToolsPanel = lazy(async () => ({ default: (await import('../tools/ToolsPanel')).ToolsPanel }))
const ChannelsPanel = lazy(() => import('../channels/ChannelsPanel'))
const McpServers = lazy(() => import('../mcp/McpServers'))


/** Configurações no mesmo tema da conversa, preservando os controles de cada painel. */
export function SettingsPanel({ agent, data, initial = 'profile', onReload }: { agent: AgentSummary; data: Onboarding; initial?: SettingsView; onReload: () => void }) {
  const { t: bee } = useTranslation('bee')
  const { t: policies } = useTranslation('policies')
  const { t: tools } = useTranslation('tools')
  const { t: channels } = useTranslation('channels')
  const { t: environments } = useTranslation('environments')
  const { t: activity } = useTranslation('activity')
  const { t: attention } = useTranslation('attention')
  const { t: skills } = useTranslation('skills')
  const { t: deliveries } = useTranslation('deliveries')
  const { t: routines } = useTranslation('routines')
  const { t: workspace } = useTranslation('workspace')
  const [view, setView] = useState<SettingsView>(initial)
  const [notice, setNotice] = useState<string | null>(null)
  const content = useRef<HTMLDivElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const focusNext = useRef(false)
  useLayoutEffect(() => {
    if (content.current) content.current.scrollTop = 0
    if (focusNext.current) heading.current?.focus({ preventScroll: true })
    focusNext.current = false
  }, [view])
  const navigate = (next: SettingsView, focusContent = true) => {
    if (next === view) return
    focusNext.current = focusContent
    setView(next); setNotice(null)
  }
  const label = (item: SettingsView) => item === 'routines' ? routines('navigation') : item === 'deliveries' ? deliveries('navigation') : item === 'skills' ? skills('navigation')
    : item === 'policies' ? policies('navigation') : item === 'usage' ? activity('navigation') : item === 'attention' ? attention('navigation') : item === 'tools' ? tools('navigation')
      : item === 'computer' ? environments('navigation') : item === 'channels' ? channels('navigation') : item === 'tasks' ? workspace('activity') : bee(`view_${item}`)
  return <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-background md:flex-row" data-settings>
    <SettingsNavigation view={view} label={label} onSelect={navigate} />
    <div ref={content} role="region" aria-labelledby="settings-heading" className="settings-content relative min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain p-4 md:p-8">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
        <header className="flex flex-col gap-1 pb-1">
          <p className="text-xs font-medium text-muted-foreground">{workspace('settings')}</p>
          <h2 ref={heading} id="settings-heading" tabIndex={-1} className="text-xl font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring/50">{label(view)}</h2>
        </header>
        {notice && <p className="form-success saved-notice flex items-center gap-2" role="status"><CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />{bee(notice)}</p>}
        {view === 'profile' && <ProfileEditor key={`${agent.id}:${agent.revision}`} agent={agent} onSaved={() => { setNotice('profileSaved'); onReload() }} onReload={onReload} />}
        {view === 'model' && <ModelEditor key={`${agent.id}:${agent.revision}`} agent={agent} vault={data.vault} onSaved={() => { setNotice('modelSaved'); onReload() }} onReload={onReload} />}
        {view === 'model' && <AgentActivation key={`activate:${agent.id}:${agent.revision}`} agent={agent} onSaved={() => { setNotice('agentActive'); onReload() }} onReload={onReload} />}
        {view === 'memories' && <MemoryPanel key={agent.id} agent={agent} />}
        {view === 'tasks' && <TaskPanel key={agent.id} agent={agent} />}
        <Suspense fallback={<LoadingNotice />}>
          {view === 'deliveries' && <DeliveryHistoryPanel key={agent.id} agent={agent} onSkills={() => navigate('skills')} />}
          {view === 'routines' && <RoutinesPanel key={agent.id} agent={agent} />}
          {view === 'skills' && <SkillsPanel key={agent.id} agent={agent} />}
          {view === 'tools' && <><ToolsPanel key={agent.id} agent={agent} /><McpServers key={`mcp-${agent.id}`} agentId={agent.id} /><section className="surface"><RecentAgentDecisions key={`decisions-${agent.id}`} agentId={agent.id} /></section></>}
          {view === 'computer' && <><ComputerWorkspace key={`computer-${agent.id}`} agentId={agent.id} /><EnvironmentPanel key={agent.id} agent={agent} /></>}
          {view === 'channels' && <ChannelsPanel key={agent.id} agentId={agent.id} />}
        </Suspense>
        {view === 'policies' && <PolicyPanel key={agent.id} agent={agent} />}
        {view === 'usage' && <ActivityPanel key={agent.id} agent={agent} onPreferences={() => navigate('attention')} />}
        {view === 'attention' && <AttentionPanel key={agent.id} agent={agent} />}
      </div>
    </div>
  </div>
}
