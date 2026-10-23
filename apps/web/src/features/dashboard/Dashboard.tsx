import { lazy, Suspense, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { onboarding } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'
import { Chat } from '../chat/Chat'
import { CreateBee } from '../setup/CreateBee'
import { ProfileEditor } from '../bee/ProfileEditor'
import { AgentActivation } from '../bee/AgentActivation'
import { ModelEditor } from '../bee/ModelEditor'
import { MemoryPanel } from '../memory/MemoryPanel'
import { TaskPanel } from '../tasks/TaskPanel'
import { PolicyPanel } from '../policies/PolicyPanel'
import { ActivityPanel } from '../activity/ActivityPanel'
import { rememberWorkspace, restoreWorkspace } from '../activity/context'
import type { WorkspaceView } from '../activity/context'
import { ComputerSummary } from '../computer/ComputerSummary'
import { SafetyControl } from '../safety/SafetyControl'
import { AttentionPanel } from '../attention/AttentionPanel'

const RoutinesPanel = lazy(async () => {
  const module = await import('../routines/RoutinesPanel')
  return { default: module.RoutinesPanel }
})

const DeliveryHistoryPanel = lazy(async () => {
  const module = await import('../deliveries/DeliveryHistoryPanel')
  return { default: module.DeliveryHistoryPanel }
})

const SkillsPanel = lazy(async () => {
  const module = await import('../skills/SkillsPanel')
  return { default: module.SkillsPanel }
})

const ComputerWorkspace = lazy(async () => {
  const module = await import('../computer/ComputerWorkspace')
  return { default: module.ComputerWorkspace }
})
const EnvironmentPanel = lazy(async () => {
  const module = await import('../environments/EnvironmentPanel')
  return { default: module.EnvironmentPanel }
})
const ToolsPanel = lazy(async () => {
  const module = await import('../tools/ToolsPanel')
  return { default: module.ToolsPanel }
})
const ChannelsPanel = lazy(() => import('../channels/ChannelsPanel'))

export function Dashboard({ userName }: { userName: string }) {
  const { t } = useTranslation('product')
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
  const { resource, reload } = useResource(onboarding)
  const [creating, setCreating] = useState(false)
  const [workspace] = useState(restoreWorkspace)
  const [selectedId, setSelectedId] = useState<string | null>(workspace.selectedId)
  const [view, setView] = useState<WorkspaceView>(workspace.view)
  useEffect(() => { rememberWorkspace(selectedId, view) }, [selectedId, view])
  const [notice, setNotice] = useState<string | null>(null)
  if (resource.state === 'loading') return <LoadingNotice />
  if (resource.state === 'error') return <div className="surface resource-error"><ErrorNotice error={resource.error} /><button className="button secondary" onClick={reload} type="button">{t('retry')}</button></div>
  const data = resource.data
  if (creating || data.agents.length === 0) return <CreateBee vault={data.vault} onCreated={(agent) => { setSelectedId(agent.id); setCreating(false); reload() }} onCancel={data.agents.length ? () => setCreating(false) : undefined} onReconcile={() => { setCreating(false); reload() }} />
  const selected = data.agents.find((agent) => agent.id === selectedId) ?? data.agents[0]

  return (
    <>
      <div className="page-heading"><div><p className="eyebrow">{t('yourHive')}</p><h1>{t('greeting', { name: userName })}</h1><p>{t('dashboardDescription')}</p></div><button type="button" className="button secondary" onClick={() => setCreating(true)}>{t('newBee')}</button></div>
      <SafetyControl />
      <div className="workspace-grid">
        <aside className="bee-sidebar" aria-label={t('yourBees')}>
          <h2 className="section-label">{t('yourBees')}</h2>
          {data.has_more && <p className="quiet-note">{t('limitedAgents')}</p>}
          <div className="bee-list">{data.agents.map((agent) => <button type="button" key={agent.id} className={`bee-select ${agent.id === selected.id ? 'selected' : ''}`} aria-pressed={agent.id === selected.id} onClick={() => { setSelectedId(agent.id); setView('conversation'); setNotice(null) }}><img src="/bee.svg" alt="" width="32" height="32" /><span><strong>{agent.name}</strong><small>{agent.purpose}</small>{agent.status !== 'active' && <small>{bee(`status_${agent.status}`)}</small>}</span></button>)}</div>
          <section className="model-summary" aria-labelledby="model-summary-heading"><h2 className="section-label" id="model-summary-heading">{t('connectedModel')}</h2>{selected.provider_config ? <><strong>{selected.provider_config.model}</strong><span className="provider-label">{t(selected.provider_config.kind === 'ollama' ? 'localProvider' : 'remoteProvider')}</span><code>{selected.provider_config.endpoint}</code><p>{t('connectionSaved')}</p></> : <p>{t('modelNotConfigured')}</p>}</section>
          <section className="environment-section" aria-labelledby="environments-heading"><h2 className="section-label" id="environments-heading">{t('environments')}</h2><ComputerSummary key={selected.id} agentId={selected.id} onOpen={() => { setView('computer'); setNotice(null) }} /></section>
        </aside>
        <div className="conversation-column">
          <nav className="bee-navigation" aria-label={bee('beeNavigation')}>{(['conversation', 'profile', 'model', 'memories', 'deliveries', 'skills', 'routines', 'policies', 'usage', 'attention', 'tools', 'computer', 'channels'] as const).map((item) => <button key={item} type="button" className={view === item ? 'current' : ''} aria-pressed={view === item} onClick={() => { setView(item); setNotice(null) }}>{item === 'routines' ? routines('navigation') : item === 'deliveries' ? deliveries('navigation') : item === 'skills' ? skills('navigation') : item === 'policies' ? policies('navigation') : item === 'usage' ? activity('navigation') : item === 'attention' ? attention('navigation') : item === 'tools' ? tools('navigation') : item === 'computer' ? environments('navigation') : item === 'channels' ? channels('navigation') : bee(`view_${item}`)}</button>)}</nav>
          {notice && <p className="form-success saved-notice" role="status">{bee(notice)}</p>}
          {view === 'profile' && <ProfileEditor key={`${selected.id}:${selected.revision}`} agent={selected} onSaved={() => { setNotice('profileSaved'); reload() }} onReload={reload} />}
          {view === 'model' && <ModelEditor key={`${selected.id}:${selected.revision}`} agent={selected} vault={data.vault} onSaved={() => { setNotice('modelSaved'); reload() }} onReload={reload} />}
          {view === 'model' && <AgentActivation key={`activate:${selected.id}:${selected.revision}`} agent={selected} onSaved={() => { setNotice('agentActive'); reload() }} onReload={reload} />}
          {view === 'memories' && <MemoryPanel key={selected.id} agent={selected} />}
          {view === 'deliveries' && <Suspense fallback={<LoadingNotice />}><DeliveryHistoryPanel key={selected.id} agent={selected} onSkills={() => setView('skills')} /></Suspense>}
          {view === 'routines' && <Suspense fallback={<LoadingNotice />}><RoutinesPanel key={selected.id} agent={selected} /></Suspense>}
          {view === 'skills' && <Suspense fallback={<LoadingNotice />}><SkillsPanel key={selected.id} agent={selected} /></Suspense>}
          {view === 'policies' && <PolicyPanel key={selected.id} agent={selected} />}
          {view === 'usage' && <ActivityPanel key={selected.id} agent={selected} onPreferences={() => setView('attention')} />}
          {view === 'attention' && <AttentionPanel key={selected.id} agent={selected} />}
          {view === 'tools' && <Suspense fallback={<LoadingNotice />}><ToolsPanel key={selected.id} agent={selected} /></Suspense>}
          {view === 'computer' && <Suspense fallback={<LoadingNotice />}><ComputerWorkspace key={`computer-${selected.id}`} agentId={selected.id} /><EnvironmentPanel key={selected.id} agent={selected} /></Suspense>}
          {view === 'channels' && <Suspense fallback={<LoadingNotice />}><ChannelsPanel key={selected.id} agentId={selected.id} /></Suspense>}
          {view === 'conversation' && <TaskPanel key={selected.id} agent={selected} />}
          {view === 'conversation' && (selected.provider_config && selected.conversation_id ? <Chat key={`${selected.id}:${selected.conversation_id}`} agent={{ ...selected, conversation_id: selected.conversation_id }} /> : <div className="surface legacy-agent"><h2>{t('modelNotConfigured')}</h2><p>{bee('legacyConfigure')}</p><button className="button primary" type="button" onClick={() => setView('model')}>{bee('configureModel')}</button></div>)}
          <div className="next-step-note"><span aria-hidden="true">↗</span><p><strong>{t('nextStepTitle')}</strong>{t('nextStepDescription')}</p></div>
        </div>
      </div>
    </>
  )
}
