import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getMemories } from '../../api/bee'
import type { MemoryRecord } from '../../api/bee'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'
import { DeleteMemory } from './DeleteMemory'
import { MemoryForm } from './MemoryForm'
import { formatDate, safeSourceUrl } from './format'

export function MemoryPanel({ agent }: { agent: AgentSummary }) {
  const { t, i18n } = useTranslation('bee')
  const { t: product } = useTranslation('product')
  const load = useCallback((signal: AbortSignal) => getMemories(agent.id, signal), [agent.id])
  const { resource, reload } = useResource(load)
  const [editor, setEditor] = useState<MemoryRecord | 'new' | null>(null)
  const [deleting, setDeleting] = useState<MemoryRecord | null>(null)
  const [scope, setScope] = useState<MemoryRecord['scope'] | 'all'>('all')
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const reconcile = () => { setEditor(null); setDeleting(null); reload() }

  if (resource.state === 'loading') return <LoadingNotice />
  if (resource.state === 'error') return <div className="surface"><ErrorNotice error={resource.error} /><button className="button secondary" type="button" onClick={reload}>{product('retry')}</button></div>
  const data = resource.data
  if (editor) return <MemoryForm key={editor === 'new' ? 'new' : `${editor.id}:${editor.revision}`} agentId={agent.id} memory={editor === 'new' ? null : editor} tasks={data.tasks} onSaved={() => { setNotice('memorySaved'); reconcile() }} onCancel={() => setEditor(null)} onReload={reconcile} />
  const records = data.memories.filter((memory) => (scope === 'all' || memory.scope === scope) && memory.content.toLocaleLowerCase().includes(query.toLocaleLowerCase()))

  return <section className="surface memory-panel" aria-labelledby="memory-heading">
    <div className="form-heading"><div><p className="section-label">{t('memoryEyebrow')}</p><h2 id="memory-heading">{t('memoryTitle')}</h2></div><button type="button" className="button primary" onClick={() => { setNotice(null); setEditor('new') }}>{t('newMemory')}</button></div>
    <p className="form-introduction">{t('memoryDescription')}</p>
    {!agent.memory_enabled && <p className="transfer-notice">{t('memoryDisabled')}</p>}
    {notice && <p className="form-success" role="status">{t(notice)}</p>}
    <div className="memory-toolbar"><label className="field" htmlFor="memory-search"><span>{t('searchMemories')}</span><input id="memory-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label className="field" htmlFor="scope-filter"><span>{t('filterScope')}</span><select id="scope-filter" value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}><option value="all">{t('allScopes')}</option><option value="user">{t('scope_user')}</option><option value="agent">{t('scope_agent')}</option>{(data.tasks.length > 0 || data.memories.some((memory) => memory.scope === 'task')) && <option value="task">{t('scope_task')}</option>}</select></label></div>
    {data.has_more && <p className="quiet-note">{t('memoryLimit')}</p>}
    {records.length === 0 && <div className="memory-empty"><h3>{t(data.memories.length ? 'noMemoryMatches' : 'memoryEmptyTitle')}</h3><p>{t(data.memories.length ? 'noMemoryMatchesHelp' : 'memoryEmptyDescription')}</p></div>}
    <div className="memory-list">{records.map((memory) => {
      const href = memory.source_ref ? safeSourceUrl(memory.source_ref) : null
      const task = data.tasks.find((task) => task.id === memory.task_id)
      return <article className="memory-card" key={memory.id}>
        <div className="memory-badges"><span>{t(`scope_${memory.scope}`)}</span><span>{t(`kind_${memory.kind}`)}</span></div><p className="memory-content">{memory.content}</p>
        <dl className="memory-details">
          {memory.scope === 'task' && <div><dt>{t('task')}</dt><dd>{task?.title ?? memory.task_id}</dd></div>}
          {memory.source_ref && <div><dt>{t('memorySource')}</dt><dd>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{memory.source_ref}</a> : memory.source_ref}</dd></div>}
          {memory.observed_at && <div><dt>{t('observedAt')}</dt><dd>{formatDate(memory.observed_at, i18n.resolvedLanguage)}</dd></div>}
          <div><dt>{t('createdAt')}</dt><dd>{formatDate(memory.created_at, i18n.resolvedLanguage)}</dd></div>
          <div><dt>{t('updatedAt')}</dt><dd>{formatDate(memory.updated_at, i18n.resolvedLanguage)}</dd></div>
        </dl>
        <div className="memory-actions"><button type="button" className="text-button" onClick={() => { setNotice(null); setEditor(memory) }}>{t('editMemory')}</button><button type="button" className="text-button destructive-text" onClick={() => { setNotice(null); setDeleting(memory) }}>{t('deleteMemory')}</button></div>
      </article>
    })}</div>
    <button type="button" className="text-button" onClick={reload}>{product('refreshState')}</button>
    {deleting && <DeleteMemory agentId={agent.id} memory={deleting} onDeleted={() => { setNotice('memoryDeleted'); reconcile() }} onCancel={() => setDeleting(null)} onReload={reconcile} />}
  </section>
}
