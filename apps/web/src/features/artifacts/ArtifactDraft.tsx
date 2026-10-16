import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { previewArtifact } from './api'
import type { ArtifactRecord, ArtifactPreview } from './api'
import { prepareBytes, prepareFile } from './mutations'
import type { Candidate } from './mutations'

export function ArtifactDraft({ agentId, conversationId, previous, seed, blocked, onConfirm, onClose }: { agentId: string; conversationId: string; previous: ArtifactRecord | null; seed: Candidate | null; blocked: boolean; onConfirm: (candidate: Candidate) => void; onClose: () => void }) {
  const { t } = useTranslation('artifacts')
  const headingId = useId()
  const [candidate, setCandidate] = useState<Candidate | null>(seed)
  const [preview, setPreview] = useState<ArtifactPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState('')
  const [name, setName] = useState(previous?.name ?? '')
  const controller = useRef<AbortController | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus(); return () => controller.current?.abort() }, [])
  async function file(file: File) {
    controller.current?.abort(); const operation = new AbortController(); controller.current = operation
    setBusy(true); setError(null)
    try { const next = await prepareFile(file, operation.signal); if (!operation.signal.aborted) { setCandidate(next); setEditing(false) } }
    catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure)) }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  async function openEditor() {
    if (!previous || busy || blocked) return
    controller.current?.abort(); const operation = new AbortController(); controller.current = operation
    setBusy(true); setError(null)
    try {
      const next = await previewArtifact(agentId, conversationId, previous, operation.signal)
      if (!operation.signal.aborted) { setPreview(next); if (!next.truncated) { setText(next.text); setName(previous.name); setEditing(true); setCandidate(null) } }
    } catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure)) }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  async function prepareEditor() {
    if (!previous || !editing || preview?.truncated !== false || blocked) return
    const operation = new AbortController(); controller.current = operation
    setBusy(true); setError(null)
    try { const next = await prepareBytes(name, previous.media_type, new TextEncoder().encode(text), operation.signal); if (!operation.signal.aborted) { setCandidate(next); setEditing(false) } }
    catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure)) }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  return <section className="artifact-draft" aria-labelledby={headingId} aria-busy={busy}>
    <div className="artifact-panel-heading"><h4 id={headingId} tabIndex={-1} ref={heading}>{t(previous ? 'reviewHeading' : 'uploadHeading', { name: previous?.name, version: previous?.version })}</h4><button type="button" className="text-button" onClick={onClose} disabled={blocked || busy}>{t('close')}</button></div>
    <p className="quiet-note">{t('localOnly')}</p>
    {previous && <p className="quiet-note">{t('reviewHelp')}</p>}
    <label className="artifact-file-label">{t(previous ? 'replacementFile' : 'chooseFile')}<input ref={fileInput} type="file" accept=".txt,.md,.csv,.json,text/plain,text/markdown,text/csv,application/json" disabled={blocked || busy} onChange={(event) => { const selected = event.target.files?.[0]; if (selected) void file(selected); event.target.value = '' }} /></label>
    <p className="quiet-note">{t('fileLimits')}</p>
    {previous && !editing && <button type="button" className="button secondary" disabled={blocked || busy} onClick={() => { void openEditor() }}>{t('openEditor')}</button>}
    {preview?.truncated && <p className="transfer-notice" role="status">{t('editorTooLarge')}</p>}
    {editing && <div className="artifact-text-editor"><label>{t('fileName')}<input value={name} maxLength={200} disabled={blocked || busy} onChange={(event) => { setName(event.target.value); setCandidate(null) }} /></label><label>{t('textContent')}<textarea value={text} rows={12} disabled={blocked || busy} onChange={(event) => { setText(event.target.value); setCandidate(null) }} /></label><p className="quiet-note">{t('editorHelp')}</p><button type="button" className="button secondary" disabled={blocked || busy} onClick={() => { void prepareEditor() }}>{t('prepareRevision')}</button></div>}
    {busy && <><p role="status">{t('readingFile')}</p><button type="button" className="text-button" onClick={() => { controller.current?.abort(); setBusy(false) }}>{t('cancelSelection')}</button></>}
    {error && <p className="form-error" role="alert">{t(error.code === 'artifact_upload_too_large' ? 'uploadTooLarge' : error.code === 'artifact_invalid_format' ? 'formatError' : error.code === 'artifact_invalid_content' ? 'contentError' : 'prepareError')}</p>}
    {candidate && <div className="artifact-candidate"><strong>{candidate.name}</strong><p>{candidate.media_type} · {t('bytes', { value: candidate.size_bytes })}</p><p className="quiet-note">{t('candidateReady')}</p><button className="button primary" type="button" disabled={blocked || busy} onClick={() => onConfirm(candidate)}>{t(previous ? 'confirmRevision' : 'confirmUpload')}</button><button className="text-button" type="button" disabled={blocked || busy} onClick={() => setCandidate(null)}>{t('cancelSelection')}</button></div>}
  </section>
}
