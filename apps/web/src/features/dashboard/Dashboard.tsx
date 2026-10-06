import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { onboarding } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'
import { Chat } from '../chat/Chat'
import { CreateBee } from '../setup/CreateBee'
import { ProfileEditor } from '../bee/ProfileEditor'
import { ModelEditor } from '../bee/ModelEditor'
import { MemoryPanel } from '../memory/MemoryPanel'
import { TaskPanel } from '../tasks/TaskPanel'
import { PolicyPanel } from '../policies/PolicyPanel'

export function Dashboard({ userName }: { userName: string }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const { t: policies } = useTranslation('policies')
  const { resource, reload } = useResource(onboarding)
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<'conversation' | 'profile' | 'model' | 'memories' | 'policies'>('conversation')
  const [notice, setNotice] = useState<string | null>(null)
  if (resource.state === 'loading') return <LoadingNotice />
  if (resource.state === 'error') return <div className="surface resource-error"><ErrorNotice error={resource.error} /><button className="button secondary" onClick={reload} type="button">{t('retry')}</button></div>
  const data = resource.data
  if (creating || data.agents.length === 0) return <CreateBee vault={data.vault} onCreated={(agent) => { setSelectedId(agent.id); setCreating(false); reload() }} onCancel={data.agents.length ? () => setCreating(false) : undefined} onReconcile={() => { setCreating(false); reload() }} />
  const selected = data.agents.find((agent) => agent.id === selectedId) ?? data.agents[0]

  return (
    <>
      <div className="page-heading"><div><p className="eyebrow">{t('yourHive')}</p><h1>{t('greeting', { name: userName })}</h1><p>{t('dashboardDescription')}</p></div><button type="button" className="button secondary" onClick={() => setCreating(true)}>{t('newBee')}</button></div>
      <div className="workspace-grid">
        <aside className="bee-sidebar" aria-label={t('yourBees')}>
          <h2 className="section-label">{t('yourBees')}</h2>
          {data.has_more && <p className="quiet-note">{t('limitedAgents')}</p>}
          <div className="bee-list">{data.agents.map((agent) => <button type="button" key={agent.id} className={`bee-select ${agent.id === selected.id ? 'selected' : ''}`} aria-pressed={agent.id === selected.id} onClick={() => { setSelectedId(agent.id); setView('conversation'); setNotice(null) }}><img src="/bee.svg" alt="" width="32" height="32" /><span><strong>{agent.name}</strong><small>{agent.purpose}</small>{agent.status !== 'active' && <small>{bee(`status_${agent.status}`)}</small>}</span></button>)}</div>
          <section className="model-summary" aria-labelledby="model-summary-heading"><h2 className="section-label" id="model-summary-heading">{t('connectedModel')}</h2>{selected.provider_config ? <><strong>{selected.provider_config.model}</strong><span className="provider-label">{t(selected.provider_config.kind === 'ollama' ? 'localProvider' : 'remoteProvider')}</span><code>{selected.provider_config.endpoint}</code><p>{t('connectionSaved')}</p></> : <p>{t('modelNotConfigured')}</p>}</section>
          <section className="environment-section" aria-labelledby="environments-heading"><h2 className="section-label" id="environments-heading">{t('environments')}</h2>{['ownComputer', 'personalComputer'].map((key) => <div className="environment-card" key={key}><h3>{t(key)}</h3><span>{t('preparing')}</span><p>{t(`${key}Description`)}</p></div>)}<p className="quiet-note">{t('environmentScope')}</p></section>
        </aside>
        <div className="conversation-column">
          <nav className="bee-navigation" aria-label={bee('beeNavigation')}>{(['conversation', 'profile', 'model', 'memories', 'policies'] as const).map((item) => <button key={item} type="button" className={view === item ? 'current' : ''} aria-pressed={view === item} onClick={() => { setView(item); setNotice(null) }}>{item === 'policies' ? policies('navigation') : bee(`view_${item}`)}</button>)}</nav>
          {notice && <p className="form-success saved-notice" role="status">{bee(notice)}</p>}
          {view === 'profile' && <ProfileEditor key={`${selected.id}:${selected.revision}`} agent={selected} onSaved={() => { setNotice('profileSaved'); reload() }} onReload={reload} />}
          {view === 'model' && <ModelEditor key={`${selected.id}:${selected.revision}`} agent={selected} vault={data.vault} onSaved={() => { setNotice('modelSaved'); reload() }} onReload={reload} />}
          {view === 'memories' && <MemoryPanel key={selected.id} agent={selected} />}
          {view === 'policies' && <PolicyPanel key={selected.id} agent={selected} />}
          {view === 'conversation' && <TaskPanel key={selected.id} agent={selected} />}
          {view === 'conversation' && (selected.provider_config && selected.conversation_id ? <Chat key={`${selected.id}:${selected.conversation_id}`} agent={{ ...selected, conversation_id: selected.conversation_id }} /> : <div className="surface legacy-agent"><h2>{t('modelNotConfigured')}</h2><p>{bee('legacyConfigure')}</p><button className="button primary" type="button" onClick={() => setView('model')}>{bee('configureModel')}</button></div>)}
          <div className="next-step-note"><span aria-hidden="true">↗</span><p><strong>{t('nextStepTitle')}</strong>{t('nextStepDescription')}</p></div>
        </div>
      </div>
    </>
  )
}
