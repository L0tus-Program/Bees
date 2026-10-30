import { lazy, Suspense, useState } from 'react'
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
import { cn } from '@/lib/utils'
import { SETTINGS_VIEWS } from './settingsViews'
import type { SettingsView } from './settingsViews'

const RoutinesPanel = lazy(async () => ({ default: (await import('../routines/RoutinesPanel')).RoutinesPanel }))
const DeliveryHistoryPanel = lazy(async () => ({ default: (await import('../deliveries/DeliveryHistoryPanel')).DeliveryHistoryPanel }))
const SkillsPanel = lazy(async () => ({ default: (await import('../skills/SkillsPanel')).SkillsPanel }))
const ComputerWorkspace = lazy(async () => ({ default: (await import('../computer/ComputerWorkspace')).ComputerWorkspace }))
const EnvironmentPanel = lazy(async () => ({ default: (await import('../environments/EnvironmentPanel')).EnvironmentPanel }))
const ToolsPanel = lazy(async () => ({ default: (await import('../tools/ToolsPanel')).ToolsPanel }))
const ChannelsPanel = lazy(() => import('../channels/ChannelsPanel'))
const McpServers = lazy(() => import('../mcp/McpServers'))


/** Telas de configuração existentes, agrupadas fora da conversa. */
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
  const label = (item: SettingsView) => item === 'routines' ? routines('navigation') : item === 'deliveries' ? deliveries('navigation') : item === 'skills' ? skills('navigation')
    : item === 'policies' ? policies('navigation') : item === 'usage' ? activity('navigation') : item === 'attention' ? attention('navigation') : item === 'tools' ? tools('navigation')
      : item === 'computer' ? environments('navigation') : item === 'channels' ? channels('navigation') : item === 'tasks' ? workspace('activity') : bee(`view_${item}`)
  return <div className="flex min-h-0 flex-1 flex-col md:flex-row">
    <nav className="flex shrink-0 gap-1 overflow-x-auto border-b p-2 md:w-52 md:flex-col md:overflow-visible md:border-r md:border-b-0" aria-label={bee('beeNavigation')}>
      {SETTINGS_VIEWS.map((item) => <button key={item} type="button" aria-pressed={view === item} onClick={() => { setView(item); setNotice(null) }}
        className={cn('cursor-pointer rounded-md border-0 px-3 py-2 text-left text-sm whitespace-nowrap text-foreground hover:bg-muted', view === item ? 'bg-muted font-semibold' : 'bg-transparent')}>{label(item)}</button>)}
    </nav>
    <div className="legacy-scope min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
      <div className="mx-auto flex max-w-4xl flex-col gap-6">
        {notice && <p className="form-success saved-notice" role="status">{bee(notice)}</p>}
        {view === 'profile' && <ProfileEditor key={`${agent.id}:${agent.revision}`} agent={agent} onSaved={() => { setNotice('profileSaved'); onReload() }} onReload={onReload} />}
        {view === 'model' && <ModelEditor key={`${agent.id}:${agent.revision}`} agent={agent} vault={data.vault} onSaved={() => { setNotice('modelSaved'); onReload() }} onReload={onReload} />}
        {view === 'model' && <AgentActivation key={`activate:${agent.id}:${agent.revision}`} agent={agent} onSaved={() => { setNotice('agentActive'); onReload() }} onReload={onReload} />}
        {view === 'memories' && <MemoryPanel key={agent.id} agent={agent} />}
        {view === 'tasks' && <TaskPanel key={agent.id} agent={agent} />}
        <Suspense fallback={<LoadingNotice />}>
          {view === 'deliveries' && <DeliveryHistoryPanel key={agent.id} agent={agent} onSkills={() => setView('skills')} />}
          {view === 'routines' && <RoutinesPanel key={agent.id} agent={agent} />}
          {view === 'skills' && <SkillsPanel key={agent.id} agent={agent} />}
          {view === 'tools' && <><ToolsPanel key={agent.id} agent={agent} /><McpServers key={`mcp-${agent.id}`} agentId={agent.id} /><section className="surface"><RecentAgentDecisions key={`decisions-${agent.id}`} agentId={agent.id} /></section></>}
          {view === 'computer' && <><ComputerWorkspace key={`computer-${agent.id}`} agentId={agent.id} /><EnvironmentPanel key={agent.id} agent={agent} /></>}
          {view === 'channels' && <ChannelsPanel key={agent.id} agentId={agent.id} />}
        </Suspense>
        {view === 'policies' && <PolicyPanel key={agent.id} agent={agent} />}
        {view === 'usage' && <ActivityPanel key={agent.id} agent={agent} onPreferences={() => setView('attention')} />}
        {view === 'attention' && <AttentionPanel key={agent.id} agent={agent} />}
      </div>
    </div>
  </div>
}
