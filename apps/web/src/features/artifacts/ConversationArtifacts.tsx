import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError, ApiError } from '../../api/client'
import { downloadArtifact } from './api'
import type { ArtifactRecord } from './api'
import { ArtifactReader } from './reader'
import type { ArtifactView } from './reader'
import { ArtifactCard } from './ArtifactCard'
import { ArtifactDraft } from './ArtifactDraft'
import { ArtifactPreview } from './ArtifactPreview'
import { idlePublication, isAttachmentSelection, prepareFile, Publication, rememberPublication, restorePublication } from './mutations'
import type { Candidate, PublicationState } from './mutations'
import './artifacts.css'

export function ConversationArtifacts(props: { agentId: string; conversationId: string; selected: ArtifactRecord[]; onSelected: (items: ArtifactRecord[]) => void; onReady: (ready: boolean) => void; blocked: boolean }) {
  return <ConversationFiles key={`${props.agentId}/${props.conversationId}`} {...props} />
}
function ConversationFiles({ agentId, conversationId, selected, onSelected, onReady, blocked }: { agentId: string; conversationId: string; selected: ArtifactRecord[]; onSelected: (items: ArtifactRecord[]) => void; onReady: (ready: boolean) => void; blocked: boolean }) {
  const { t } = useTranslation('artifacts')
  const [view, setView] = useState<ArtifactView>({ page: null, loading: true, error: null })
  const [publication, setPublication] = useState<PublicationState>(idlePublication)
  const [draft, setDraft] = useState<{ previous: ArtifactRecord | null; seed: Candidate | null } | null>(null)
  const [preview, setPreview] = useState<ArtifactRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [downloadSent, setDownloadSent] = useState(false)
  const reader = useRef<ArtifactReader | null>(null)
  const publisher = useRef<Publication | null>(null)
  const downloadOperation = useRef<AbortController | null>(null)
  const releases = useRef(new Set<() => void>())
  const addButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const known = new Map(view.page?.items.map((item) => [item.id, item]))
    onReady(!view.loading && !view.error && selected.every((item) => { const current = known.get(item.id); return !current || current.status === 'ready' && current.revision === item.revision && current.sha256 === item.sha256 }))
  }, [view, selected, onReady])
  useEffect(() => {
    const observer = new ArtifactReader(agentId, conversationId, setView, true)
    reader.current = observer
    const command = new Publication(agentId, conversationId, (next) => {
      setPublication(next); rememberPublication(next)
      if (next.phase === 'confirmed') { setDraft(null); void observer.load(); addButton.current?.focus() }
    })
    publisher.current = command
    const prior = restorePublication(agentId, conversationId); if (prior) command.restore(prior)
    void observer.load()
    const pending = releases.current
    return () => { observer.stop(); command.stop(); downloadOperation.current?.abort(); pending.forEach((release) => release()); pending.clear(); onReady(false) }
  }, [agentId, conversationId, onReady])
  const inFlight = ['sending', 'checking'].includes(publication.phase)
  const unresolved = !!publication.intent && ['uncertain', 'checking', 'retryable', 'sending'].includes(publication.phase)
  const stale = view.loading || !!view.error
  const mutationBlocked = blocked || stale || busy || unresolved
  function choose(artifact: ArtifactRecord) {
    if (blocked || stale || busy || inFlight) return
    setError(null)
    const next = selected.some((item) => item.id === artifact.id) ? selected.filter((item) => item.id !== artifact.id) : [...selected, artifact]
    if (!isAttachmentSelection(next, agentId, conversationId)) { setError(new ApiError('artifact_context_too_large')); return }
    onSelected(next)
  }
  async function download(artifact: ArtifactRecord) {
    if (busy || stale || blocked) return
    const controller = new AbortController(); downloadOperation.current = controller
    setBusy(true); setError(null); setDownloadSent(false)
    try {
      const blob = await downloadArtifact(agentId, artifact.task_id, artifact, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = artifact.name
      const release = () => { clearTimeout(timer); URL.revokeObjectURL(url); releases.current.delete(release) }
      const timer = setTimeout(release, 30000); releases.current.add(release)
      try { document.body.append(anchor); anchor.click() } catch (failure) { release(); throw failure } finally { anchor.remove() }
      setDownloadSent(true)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  async function reselect(file: File) {
    const operation = new AbortController(); downloadOperation.current = operation
    setBusy(true); setError(null)
    try { const candidate = await prepareFile(file, operation.signal); if (!operation.signal.aborted) publisher.current?.reselect(candidate) }
    catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure)) }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  function closeDraft() { setDraft(null); addButton.current?.focus() }
  return <section className="conversation-artifacts" aria-labelledby="conversation-artifacts-heading">
    <div className="artifact-panel-heading"><h3 id="conversation-artifacts-heading">{t('conversationHeading')}</h3><button className="text-button" type="button" disabled={view.loading || inFlight || busy} onClick={() => { void reader.current?.load() }}>{t('refresh')}</button></div>
    <p className="quiet-note">{t('localOnly')}</p>
    <button ref={addButton} className="button secondary" type="button" disabled={mutationBlocked} onClick={() => { publisher.current?.clear(); setDraft({ previous: null, seed: null }); setPreview(null) }}>{t('attachFile')}</button>
    {view.loading && <p role="status">{t('loading')}</p>}
    {view.error && <p className="form-error" role="status">{t('readError')} {view.page && t('stale')}</p>}
    {view.page?.items.length === 0 && !stale && <p className="quiet-note">{t('conversationEmpty')}</p>}
    {error && <p className="form-error" role="alert">{t(error.code === 'artifact_context_too_large' ? 'contextLimit' : error.code === 'artifact_candidate_mismatch' ? 'candidateMismatch' : 'downloadError')}</p>}
    {downloadSent && <p className="quiet-note" role="status">{t('sent')}</p>}
    {publication.phase !== 'idle' && <div className="artifact-publication" role="status">
      <strong>{publication.intent?.name}</strong><p>{t(publication.phase === 'retryable' && publication.artifact?.status === 'draft' ? 'publication_incomplete' : `publication_${publication.phase}`)}</p>
      {publication.phase === 'rejected' && publication.error && <p>{t(publication.error.status === 413 ? 'uploadTooLarge' : 'contentError')}</p>}
      {publication.intent && <details><summary>{t('publicationIdentity')}</summary><code>{publication.intent.id}</code><p>{t('bytes', { value: publication.intent.size_bytes })}</p><code>{publication.intent.sha256}</code></details>}
      {inFlight && <button type="button" className="text-button" onClick={() => publisher.current?.cancelTransport()}>{t('stopTransport')}</button>}
      {['uncertain', 'retryable'].includes(publication.phase) && <button type="button" className="button secondary" disabled={blocked || busy || inFlight} onClick={() => { void publisher.current?.reconcile() }}>{t('checkPublication')}</button>}
      {publication.phase === 'retryable' && <>
        {!publication.candidate && <label className="artifact-file-label">{t('reselectSameFile')}<input type="file" accept=".txt,.md,.csv,.json" disabled={blocked || busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void reselect(file); event.target.value = '' }} /></label>}
        <button type="button" className="button primary" disabled={blocked || stale || busy || !publication.candidate} onClick={() => { void publisher.current?.send() }}>{t('retrySamePublication')}</button>
      </>}
      {publication.phase === 'conflict' && <p>{t('conflictHelp')}</p>}
    </div>}
    {draft && <ArtifactDraft key={draft.previous?.id ?? 'new'} agentId={agentId} conversationId={conversationId} previous={draft.previous} seed={draft.seed} blocked={mutationBlocked} onClose={closeDraft} onConfirm={(candidate) => {
      if (mutationBlocked) return
      publisher.current?.choose(candidate, draft.previous); setDraft(null); void publisher.current?.send()
    }} />}
    {preview && <ArtifactPreview key={preview.id} artifact={preview} onClose={() => { setPreview(null); addButton.current?.focus() }} />}
    {selected.length > 0 && <div className="artifact-selected"><p>{t('selectedSummary', { count: selected.length, bytes: selected.reduce((sum, item) => sum + item.size_bytes, 0) })}</p><ul>{selected.map((item) => <li key={item.id}>{item.name} · {t('version')} {item.version} <button type="button" className="text-button" disabled={blocked || inFlight} onClick={() => onSelected(selected.filter((entry) => entry.id !== item.id))} aria-label={`${t('removeSelection')}: ${item.name}`}>{t('removeSelection')}</button></li>)}</ul><p className="quiet-note">{t('selectedHelp')}</p></div>}
    <div className="artifact-cards">{view.page?.items.map((artifact) => <ArtifactCard key={artifact.id} artifact={artifact} busy={busy} blocked={blocked || stale || busy || inFlight} onDownload={() => { void download(artifact) }} onPreview={() => { setPreview(artifact); setDraft(null) }} onReview={unresolved ? undefined : () => { const seed = publication.phase === 'conflict' ? publication.candidate : null; publisher.current?.clear(); setPreview(null); setDraft({ previous: artifact, seed }) }} selected={selected.some((item) => item.id === artifact.id)} onSelect={() => choose(artifact)} />)}</div>
    {view.page?.has_more && <button type="button" className="button secondary" disabled={view.loading || !!view.error || inFlight || busy} onClick={() => { if (view.page?.next_offset != null) void reader.current?.load(view.page.next_offset) }}>{t('more')}</button>}
  </section>
}
