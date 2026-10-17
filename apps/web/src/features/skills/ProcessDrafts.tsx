import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { getDeliveries, getDelivery } from '../../api/deliveryHistory'
import type { DeliveryItem, DeliveryPage } from '../../api/deliveryHistory'
import { checkedProcessIntent, processSkillIntent, sendSkill } from '../../api/skills'
import type { ProcessReference, SkillDefinition, SkillIntent } from '../../api/skills'
import { ErrorNotice } from '../../components/Feedback'
import { SkillEditor } from './SkillContent'

const storageKey = (agentId: string) => `bees.skill-process-pending.v1.${agentId}`
function remembered(agentId: string): { intent: SkillIntent | null; corrupt: boolean } {
  let raw: string | null
  try { raw = sessionStorage.getItem(storageKey(agentId)) } catch { return { intent: null, corrupt: false } }
  try {
    return { intent: raw ? checkedProcessIntent(JSON.parse(raw), agentId) : null, corrupt: false }
  } catch { return { intent: null, corrupt: true } }
}
const template = (): SkillDefinition => ({ name: '', purpose: '', inputs: [], steps: [{ title: '', instruction: '' }], resources: { environment: 'none', tools: [] }, output: '', completion: '' })

export function ProcessDrafts({ agentId, disabled, onLock, onSaved }: { agentId: string; disabled: boolean; onLock: (locked: boolean) => void; onSaved: () => void }) {
  const { t } = useTranslation('skills')
  const [recovery] = useState(() => remembered(agentId))
  const [open, setOpen] = useState(recovery.intent !== null || recovery.corrupt)
  const [pending, setPending] = useState(recovery.intent)
  const [page, setPage] = useState<DeliveryPage | null>(null)
  const [query, setQuery] = useState('')
  const [source, setSource] = useState<{ item: DeliveryItem; content: string; reference: ProcessReference } | null>(null)
  const [sources, setSources] = useState('')
  const [note, setNote] = useState('')
  const [remaining, setRemaining] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [storageError, setStorageError] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const lock = useRef(false)
  const heading = useRef<HTMLHeadingElement | null>(null)
  const trigger = useRef<HTMLButtonElement | null>(null)
  const wasOpen = useRef(false)
  useEffect(() => { onLock(open || pending !== null || recovery.corrupt) }, [onLock, open, pending, recovery.corrupt])
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (open) heading.current?.focus()
    else if (wasOpen.current) trigger.current?.focus()
    wasOpen.current = open
  }, [open, source])
  async function load(cursor: string | null = null) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(null)
    const request = new AbortController(); controller.current = request
    try {
      const value = await getDeliveries(agentId, { query: cursor ? page!.scope.query : query, task_id: null, conversation_id: null }, cursor, request.signal)
      if (!request.signal.aborted) setPage(value)
    } catch (failure) { if (!request.signal.aborted) setError(asApiError(failure)) }
    finally { if (!request.signal.aborted) { lock.current = false; setBusy(false) } }
  }
  async function select(item: DeliveryItem) {
    if (lock.current) return
    lock.current = true; setBusy(true); setError(null)
    const request = new AbortController(); controller.current = request
    try {
      const value = await getDelivery(agentId, item, request.signal)
      if (value.kind !== 'result' || value.item.partial || !value.item.task_id || !value.item.run_id) throw new ApiError('invalid_response')
      const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value.content))
      if (!request.signal.aborted) {
        setSource({ item: value.item, content: value.content, reference: { task_id: value.item.task_id, run_id: value.item.run_id, message_id: value.item.id, result_sha256: Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('') } })
        setSources(''); setNote(''); setRemaining(''); setConfirmed(false)
      }
    } catch (failure) { if (!request.signal.aborted) setError(asApiError(failure)) }
    finally { if (!request.signal.aborted) { lock.current = false; setBusy(false) } }
  }
  async function send(intent: SkillIntent) {
    if (lock.current) return
    // Preserve the exact UUID before the first write; reload cannot replace an uncertain request.
    try { sessionStorage.setItem(storageKey(agentId), JSON.stringify(checkedProcessIntent(intent, agentId))); setStorageError(false) }
    catch { setStorageError(true); return }
    lock.current = true; setBusy(true); setPending(intent); setError(null)
    const request = new AbortController(); controller.current = request
    try {
      await sendSkill(intent, request.signal)
      if (request.signal.aborted) return
      sessionStorage.removeItem(storageKey(agentId)); setPending(null); setSource(null); setOpen(false); onSaved()
    } catch (failure) {
      if (request.signal.aborted) return
      const problem = asApiError(failure); setError(problem)
      if (problem.status >= 400 && problem.status < 500 && problem.status !== 408) {
        try { sessionStorage.removeItem(storageKey(agentId)); setPending(null) } catch { setStorageError(true) }
      }
    } finally { if (!request.signal.aborted) { lock.current = false; setBusy(false) } }
  }
  function save(definition: SkillDefinition) {
    if (!source || !confirmed) return
    try {
      const review = { sources: sources.split('\n').map((value) => value.trim()).filter(Boolean), review_note: note, pending: remaining, confirmed }
      void send(processSkillIntent(agentId, definition, source.reference, review))
    } catch (failure) { setError(asApiError(failure)) }
  }
  if (!open) return <button ref={trigger} type="button" className="button secondary" disabled={disabled} onClick={() => { setOpen(true); void load() }}>{t('fromProcess')}</button>
  const blocked = disabled || busy || pending !== null || recovery.corrupt
  return <section className="skill-process" aria-busy={busy}>
    <h3 ref={heading} tabIndex={-1}>{t('fromProcess')}</h3>
    <p>{t('processHelp')}</p>
    {error && <ErrorNotice error={error} />}
    {storageError && <p role="alert">{t('processStorage')}</p>}
    {recovery.corrupt && <p role="alert">{t('processRecoveryBlocked')}</p>}
    {pending && <div role="status"><p>{t('lost')}</p><button type="button" className="button secondary" disabled={disabled || busy} onClick={() => { void send(pending) }}>{t('verify')}</button></div>}
    {!source && !pending && !recovery.corrupt && <>
      <form onSubmit={(event) => { event.preventDefault(); void load() }}><label className="field">{t('processSearch')}<input maxLength={200} value={query} disabled={blocked} onChange={(event) => setQuery(event.target.value)} /></label><button type="submit" className="button secondary" disabled={blocked}>{t('processFind')}</button></form>
      {page && <ul>{page.items.filter((item) => item.kind === 'result' && !item.partial).map((item) => <li key={item.id}><button type="button" className="text-button" disabled={blocked} onClick={() => { void select(item) }}>{item.title}</button><small> · {item.run_id}</small></li>)}</ul>}
      {page && !page.items.some((item) => item.kind === 'result' && !item.partial) && <p>{t('processEmpty')}</p>}
      {page?.next_cursor && <button type="button" className="button secondary" disabled={blocked} onClick={() => { void load(page.next_cursor) }}>{t('more')}</button>}
    </>}
    {source && <>
      <h4>{source.item.title}</h4><p className="quiet-note">{t('processUntrusted')}</p>
      <details><summary>{t('processResult')}</summary><pre className="skill-process-result">{source.content}</pre></details>
      <dl><dt>{t('processTask')}</dt><dd>{source.reference.task_id}</dd><dt>{t('processRun')}</dt><dd>{source.reference.run_id}</dd><dt>SHA-256</dt><dd>{source.reference.result_sha256}</dd></dl>
      <fieldset disabled={blocked}>
        <label className="field">{t('processSources')}<textarea required maxLength={20500} value={sources} onChange={(event) => { setSources(event.target.value); setConfirmed(false) }} /></label>
        <label className="field">{t('processReview')}<textarea required maxLength={2048} value={note} onChange={(event) => { setNote(event.target.value); setConfirmed(false) }} /></label>
        <label className="field">{t('processPending')}<textarea required maxLength={2048} value={remaining} onChange={(event) => { setRemaining(event.target.value); setConfirmed(false) }} /></label>
        <label className="checkbox-label"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />{t('processConfirm')}</label>
      </fieldset>
      <SkillEditor initial={{ definition: template() }} disabled={blocked} canSave={confirmed && !!sources.trim() && !!note.trim() && !!remaining.trim()} onSave={save} onCancel={() => { if (!blocked) { setSource(null); setConfirmed(false) } }} />
      {!confirmed && <p>{t('processNeedsReview')}</p>}
    </>}
    <button type="button" className="button secondary" disabled={blocked} onClick={() => { setOpen(false); setSource(null) }}>{t('cancel')}</button>
  </section>
}
