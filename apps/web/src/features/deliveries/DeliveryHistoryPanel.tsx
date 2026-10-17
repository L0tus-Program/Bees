import { useEffect, useId, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentSummary } from '../../api/onboarding'
import { deliveryReference, getDelivery } from '../../api/deliveryHistory'
import type { DeliveryContent, DeliveryItem, DeliveryScope } from '../../api/deliveryHistory'
import { TaskDetail } from '../tasks/TaskDetail'
import { TaskTime } from '../tasks/TaskTime'
import { ArtifactCard } from '../artifacts/ArtifactCard'
import { ArtifactPreview } from '../artifacts/ArtifactPreview'
import { downloadArtifact } from '../artifacts/api'
import { DeliveryReader } from './reader'
import type { DeliveryView } from './reader'
import '../artifacts/artifacts.css'
import './deliveries.css'

const emptyScope = (): DeliveryScope => ({ query: '', conversation_id: null, task_id: null })
const uuidPattern = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
export function DeliveryHistoryPanel({ agent, onSkills }: { agent: AgentSummary; onSkills: () => void }) {
  return <DeliveryWorkspace key={agent.id} agent={agent} onSkills={onSkills} />
}
function DeliveryWorkspace({ agent, onSkills }: { agent: AgentSummary; onSkills: () => void }) {
  const { t } = useTranslation('deliveries')
  const headingId = useId()
  const [draft, setDraft] = useState(emptyScope)
  const [search, setSearch] = useState({ scope: emptyScope(), generation: 0 })
  function submit(event: FormEvent) {
    event.preventDefault()
    setSearch((old) => ({ scope: { query: draft.query.trim(), conversation_id: draft.conversation_id?.trim().toLowerCase() || null, task_id: draft.task_id?.trim().toLowerCase() || null }, generation: old.generation + 1 }))
  }
  return <section className="surface delivery-history" aria-labelledby={headingId}>
    <h2 id={headingId}>{t('title')}</h2><p>{t('description')}</p>
    <form className="delivery-search" onSubmit={submit}>
      <label className="field delivery-query">{t('query')}<input maxLength={200} value={draft.query} onChange={(event) => setDraft({ ...draft, query: event.target.value })} /></label>
      <label className="field">{t('conversation')}<input pattern={uuidPattern} value={draft.conversation_id ?? ''} onChange={(event) => setDraft({ ...draft, conversation_id: event.target.value || null })} /></label>
      <label className="field">{t('task')}<input pattern={uuidPattern} value={draft.task_id ?? ''} onChange={(event) => setDraft({ ...draft, task_id: event.target.value || null })} /></label>
      <button className="button primary" type="submit">{t('search')}</button>
    </form>
    <DeliveryResults key={search.generation} agent={agent} scope={search.scope} onSkills={onSkills} />
  </section>
}
function DeliveryResults({ agent, scope, onSkills }: { agent: AgentSummary; scope: DeliveryScope; onSkills: () => void }) {
  const { t } = useTranslation('deliveries')
  const [view, setView] = useState<DeliveryView>({ page: null, loading: true, error: null, cancelled: false })
  const reader = useRef<DeliveryReader | null>(null)
  const heading = useRef<HTMLParagraphElement>(null)
  const returnFocus = useRef<HTMLButtonElement | null>(null)
  const [selected, setSelected] = useState<DeliveryItem | null>(null)
  const [task, setTask] = useState<string | null>(null)
  const [routineNotice, setRoutineNotice] = useState(false)
  const taskReturnFocus = useRef<HTMLButtonElement | null>(null)
  useEffect(() => {
    const resource = new DeliveryReader(agent.id, scope, setView); reader.current = resource
    void resource.load()
    return () => { resource.stop(); reader.current = null }
  }, [agent.id, scope])
  useEffect(() => { if (view.page && !view.loading) heading.current?.focus() }, [view.page, view.loading])
  function close() { setSelected(null); setTask(null); returnFocus.current?.focus() }
  return <>
    <div className="delivery-shortcuts"><h3>{t('shortcuts')}</h3><p>{t('shortcutHelp')}</p>
      <button className="button secondary" type="button" onClick={onSkills}>{t('skills')}</button>{' '}
      <button className="button secondary" type="button" onClick={() => setRoutineNotice(true)}>{t('routines')}</button>
      {routineNotice && <p role="status">{t('routineUnavailable')}</p>}
    </div>
    <div aria-busy={view.loading}>
      {view.loading && <><p role="status">{t('loading')}</p><button type="button" className="text-button" onClick={() => reader.current?.cancel()}>{t('cancel')}</button></>}
      {view.cancelled && <p role="status">{t('cancelled')}</p>}
      {view.error && <p className="form-error" role="alert" tabIndex={-1} ref={(node) => node?.focus()}>{t('readError')}</p>}
      {view.page && <>
        <p tabIndex={-1} ref={heading} role="status">{t('limit', { count: view.page.items.length, limit: view.page.limit })}<br />{t('boundary')}: <TaskTime value={view.page.until} /></p>
        {!view.page.items.length && !view.loading && <p>{t('empty')}</p>}
        <ol className="delivery-list">{view.page.items.map((item) => <li key={`${item.kind}:${item.id}`}>
          <h3>{item.title}</h3><p>{t(item.kind)} · {t('version')} {item.version} · {t(item.status)}</p>
          <p><TaskTime value={item.created_at} /></p>{item.summary && <p className="delivery-summary">{item.summary}</p>}
          {item.partial && <p className="transfer-notice">{t('partial')}</p>}
          <button type="button" className="button secondary" aria-label={`${t('open')}: ${item.title} · ${t('version')} ${item.version}`} disabled={view.loading || !!view.error} onClick={(event) => { returnFocus.current = event.currentTarget; setTask(null); setSelected(item) }}>{t('open')}</button>
        </li>)}</ol>
        {view.page.next_cursor !== null && <button type="button" className="button secondary" disabled={view.loading || !!view.error || view.cancelled} onClick={() => { void reader.current?.load(true) }}>{t('more')}</button>}
      </>}
    </div>
    {selected && <DeliverySelection key={`${selected.kind}:${selected.id}`} item={selected} onClose={close} onTask={(taskId, trigger) => { taskReturnFocus.current = trigger; setTask(taskId) }} />}
    {task && <div className="surface"><TaskDetail key={task} agent={agent} taskId={task} onClose={() => { setTask(null); taskReturnFocus.current?.focus() }} onRefresh={() => {}} /></div>}
  </>
}
function DeliverySelection({ item, onClose, onTask }: { item: DeliveryItem; onClose: () => void; onTask: (task: string, trigger: HTMLButtonElement) => void }) {
  const { t } = useTranslation('deliveries')
  const headingId = useId()
  const heading = useRef<HTMLHeadingElement>(null)
  const reference = useRef<HTMLTextAreaElement>(null)
  const [data, setData] = useState<DeliveryContent | null>(null)
  const [error, setError] = useState(false)
  const [copy, setCopy] = useState<'copied' | 'copyError' | null>(null)
  const [download, setDownload] = useState<'downloading' | 'downloaded' | 'downloadError' | null>(null)
  const [preview, setPreview] = useState(false)
  const operation = useRef<AbortController | null>(null)
  const alive = useRef(true)
  const downloadUrls = useRef(new Set<string>())
  useEffect(() => {
    alive.current = true
    const controller = new AbortController(); heading.current?.focus()
    void getDelivery(item.agent_id, item, controller.signal).then((value) => { if (!controller.signal.aborted) setData(value) }, () => { if (!controller.signal.aborted) setError(true) })
    const urls = downloadUrls.current
    return () => { alive.current = false; controller.abort(); operation.current?.abort(); urls.forEach((url) => URL.revokeObjectURL(url)); urls.clear() }
  }, [item])
  async function copyReference() {
    try { await navigator.clipboard.writeText(deliveryReference(item)); if (alive.current) setCopy('copied') }
    catch { if (alive.current) { setCopy('copyError'); reference.current?.focus(); reference.current?.select() } }
  }
  async function downloadVersion() {
    if (data?.kind !== 'artifact' || download === 'downloading') return
    const controller = new AbortController(); operation.current = controller; setDownload('downloading')
    try {
      const blob = await downloadArtifact(item.agent_id, item.task_id, data.artifact, controller.signal)
      if (controller.signal.aborted || !alive.current) return
      const url = URL.createObjectURL(blob); downloadUrls.current.add(url)
      const link = document.createElement('a'); link.href = url; link.download = data.artifact.name
      try { document.body.append(link); link.click() } finally { link.remove() }
      setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.current.delete(url) }, 30000)
      setDownload('downloaded')
    } catch { if (!controller.signal.aborted && alive.current) setDownload('downloadError') }
  }
  return <section className="delivery-selection" aria-labelledby={headingId}>
    <div className="form-heading"><h3 ref={heading} tabIndex={-1} id={headingId}>{t('details')}: {item.title}</h3><button type="button" className="text-button" onClick={onClose}>{t('close')}</button></div>
    <dl className="delivery-links"><dt>{t('identity')}</dt><dd><code>{item.id}</code></dd><dt>{t('task')}</dt><dd><code>{item.task_id ?? t('noTask')}</code></dd><dt>{t('conversation')}</dt><dd><code>{item.conversation_id ?? t('noConversation')}</code></dd><dt>{t('run')}</dt><dd><code>{item.run_id ?? '—'}</code></dd><dt>{t('version')}</dt><dd>{item.version}</dd><dt>{t('revision')}</dt><dd>{item.revision}</dd></dl>
    {!data && !error && <p role="status">{t('loading')}</p>}
    {error && <p className="form-error" role="alert">{t('readError')}</p>}
    {data?.kind === 'result' && <>{data.item.partial && <p className="transfer-notice">{t('partial')}</p>}<pre className="delivery-content" tabIndex={0}>{data.content}</pre></>}
    {data?.kind === 'artifact' && <><ArtifactCard artifact={data.artifact} busy={download === 'downloading'} blocked={download === 'downloading'} onDownload={() => { void downloadVersion() }} onPreview={data.artifact.conversation_id ? () => setPreview(true) : undefined} />
      {download && <p role={download === 'downloadError' ? 'alert' : 'status'}>{t(download)}</p>}
      {preview && <ArtifactPreview artifact={data.artifact} onClose={() => { setPreview(false); heading.current?.focus() }} />}</>}
    {item.task_id && <button type="button" className="button secondary" onClick={(event) => onTask(item.task_id!, event.currentTarget)}>{t('openTask')}</button>}
    <label className="field">{t('reference')}<textarea ref={reference} rows={4} readOnly value={deliveryReference(item)} onFocus={(event) => event.target.select()} /></label>
    <p className="quiet-note">{t('referenceHelp')}</p><button className="button secondary" type="button" onClick={() => { void copyReference() }}>{t('copy')}</button>
    {copy && <p role={copy === 'copyError' ? 'alert' : 'status'}>{t(copy)}</p>}
  </section>
}
