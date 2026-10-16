import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { downloadArtifact } from './api'
import type { ArtifactRecord } from './api'
import { ArtifactReader } from './reader'
import type { ArtifactView } from './reader'
import { ArtifactCard } from './ArtifactCard'
import { ArtifactPreview } from './ArtifactPreview'
import { TaskArtifactReview } from './TaskArtifactReview'
import './artifacts.css'

export function TaskArtifacts({ agentId, taskId }: { agentId: string; taskId: string }) {
  return <ArtifactList key={`${agentId}/${taskId}`} agentId={agentId} taskId={taskId} />
}

function ArtifactList({ agentId, taskId }: { agentId: string; taskId: string }) {
  const { t } = useTranslation('artifacts')
  const [view, setView] = useState<ArtifactView>({ page: null, loading: true, error: null })
  const reader = useRef<ArtifactReader | null>(null)
  const operation = useRef<AbortController | null>(null)
  const downloads = useRef(new Set<() => void>())
  const panelHeading = useRef<HTMLHeadingElement>(null)
  const [download, setDownload] = useState<{ id: string; busy: boolean; error: ApiError | null; sent: boolean } | null>(null)
  const [review, setReview] = useState<ArtifactRecord | null>(null)
  const [preview, setPreview] = useState<ArtifactRecord | null>(null)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewConfirmed, setReviewConfirmed] = useState(false)
  useEffect(() => {
    const observer = new ArtifactReader(agentId, taskId, setView); reader.current = observer
    void observer.load()
    const pendingDownloads = downloads.current
    return () => { observer.stop(); reader.current = null; operation.current?.abort(); pendingDownloads.forEach((release) => release()); pendingDownloads.clear() }
  }, [agentId, taskId])
  async function downloadVersion(artifact: ArtifactRecord) {
    if (download?.busy || view.loading || view.error || artifact.status !== 'ready') return
    const controller = new AbortController(); operation.current = controller
    setDownload({ id: artifact.id, busy: true, error: null, sent: false })
    try {
      const blob = await downloadArtifact(agentId, taskId, artifact, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = artifact.name
      const release = () => { clearTimeout(timer); URL.revokeObjectURL(url); downloads.current.delete(release) }
      const timer = setTimeout(release, 30000)
      downloads.current.add(release)
      try { document.body.append(anchor); anchor.click() } catch (failure) { release(); throw failure } finally { anchor.remove() }
      setDownload({ id: artifact.id, busy: false, error: null, sent: true })
    } catch (failure) { if (!controller.signal.aborted) setDownload({ id: artifact.id, busy: false, error: asApiError(failure), sent: false }) }
  }
  const downloadError = download?.error?.code
  const errorMessage = downloadError === 'artifact_integrity_failed' || downloadError === 'artifact_blob_missing' ? 'integrityError' : downloadError === 'artifact_not_ready' ? 'notReadyError' : downloadError === 'artifact_download_too_large' ? 'sizeError' : 'downloadError'
  return <section className="task-artifacts" aria-labelledby={`task-artifacts-${taskId}`} aria-busy={view.loading}>
    <div className="artifact-panel-heading"><h4 ref={panelHeading} tabIndex={-1} id={`task-artifacts-${taskId}`}>{t('heading')}</h4><button className="text-button" type="button" disabled={view.loading || download?.busy} onClick={() => { setDownload(null); void reader.current?.load() }}>{t('refresh')}</button></div>
    <p className="quiet-note">{t('scope')}</p>
    {view.loading && <p role="status" className="quiet-note">{t('loading')}</p>}
    {view.error && <p role="status" className="form-error">{t('readError')}{view.page && <> {t('stale')}</>}</p>}
    {view.page?.items.length === 0 && !view.loading && !view.error && <p className="quiet-note">{t('empty')}</p>}
    {download?.error && <p role="alert" className="form-error">{t(errorMessage)}</p>}
    {download?.sent && <p role="status" className="quiet-note">{t('sent')}</p>}
    {reviewConfirmed && <p className="quiet-note" role="status">{t('publication_confirmed')}</p>}
    {review && <TaskArtifactReview previous={review} blocked={view.loading || !!view.error || !!download?.busy} onPending={setReviewBusy} onChanged={() => { setReview(null); setReviewConfirmed(true); void reader.current?.load(); panelHeading.current?.focus() }} onClose={() => { setReview(null); panelHeading.current?.focus() }} />}
    {preview && <ArtifactPreview key={preview.id} artifact={preview} onClose={() => { setPreview(null); panelHeading.current?.focus() }} />}
    <div className="artifact-cards">{view.page?.items.map((artifact) => <ArtifactCard key={artifact.id} artifact={artifact} busy={download?.id === artifact.id && download.busy} blocked={view.loading || !!view.error || !!download?.busy || reviewBusy} onDownload={() => { void downloadVersion(artifact) }} onPreview={artifact.conversation_id ? () => { setPreview(artifact) } : undefined} onReview={artifact.conversation_id ? () => { setReviewConfirmed(false); setReview(artifact) } : undefined} />)}</div>
    {view.page?.has_more && <button className="button secondary" type="button" disabled={view.loading || !!view.error || download?.busy} onClick={() => { if (view.page?.next_offset !== null) void reader.current?.load(view.page?.next_offset) }}>{t('more')}</button>}
  </section>
}
