import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ArtifactRecord } from './api'
import { ArtifactDraft } from './ArtifactDraft'
import { idlePublication, prepareFile, Publication, rememberPublication, restorePublication } from './mutations'
import type { PublicationState } from './mutations'

export function TaskArtifactReview({ previous, blocked, onChanged, onClose, onPending }: { previous: ArtifactRecord; blocked: boolean; onChanged: () => void; onClose: () => void; onPending: (pending: boolean) => void }) {
  const { t } = useTranslation('artifacts')
  const [state, setState] = useState<PublicationState>(idlePublication)
  const [error, setError] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const publisher = useRef<Publication | null>(null)
  const controller = useRef<AbortController | null>(null)
  const { agent_id: agentId, conversation_id: conversationId } = previous
  const changed = useRef(onChanged)
  useEffect(() => { changed.current = onChanged }, [onChanged])
  useEffect(() => {
    if (!conversationId) return
    const operation = new Publication(agentId, conversationId, (next) => { setState(next); rememberPublication(next); onPending(['sending', 'checking', 'uncertain', 'retryable'].includes(next.phase)); if (next.phase === 'confirmed') changed.current() })
    publisher.current = operation
    const prior = restorePublication(agentId, conversationId); if (prior) operation.restore(prior)
    return () => { operation.stop(); controller.current?.abort(); onPending(false) }
  }, [agentId, conversationId, onPending])
  const pending = !!state.intent && ['sending', 'checking', 'uncertain', 'retryable'].includes(state.phase)
  const inFlight = ['sending', 'checking'].includes(state.phase)
  async function reselect(file: File) {
    controller.current?.abort(); const operation = new AbortController(); controller.current = operation
    setReading(true); setError(null)
    try { const candidate = await prepareFile(file, operation.signal); if (!operation.signal.aborted) publisher.current?.reselect(candidate) }
    catch (failure) { if (!operation.signal.aborted) setError(asApiError(failure).code) }
    finally { if (!operation.signal.aborted) setReading(false) }
  }
  if (!conversationId) return null
  return <section className="artifact-review-operation">
    {state.phase !== 'idle' && <div className="artifact-publication" role="status"><strong>{state.intent?.name}</strong><p>{t(state.phase === 'retryable' && state.artifact?.status === 'draft' ? 'publication_incomplete' : `publication_${state.phase}`)}</p>
      {state.intent && <details><summary>{t('publicationIdentity')}</summary><code>{state.intent.id}</code><br /><code>{state.intent.sha256}</code></details>}
      {inFlight && <button type="button" className="text-button" onClick={() => publisher.current?.cancelTransport()}>{t('stopTransport')}</button>}
      {['uncertain', 'retryable'].includes(state.phase) && <button type="button" className="button secondary" disabled={blocked || reading} onClick={() => { void publisher.current?.reconcile() }}>{t('checkPublication')}</button>}
      {state.phase === 'retryable' && <>{!state.candidate && <label className="artifact-file-label">{t('reselectSameFile')}<input type="file" accept=".txt,.md,.csv,.json" disabled={blocked || reading} onChange={(event) => { const file = event.target.files?.[0]; if (file) void reselect(file); event.target.value = '' }} /></label>}<button type="button" className="button primary" disabled={blocked || reading || !state.candidate} onClick={() => { void publisher.current?.send() }}>{t('retrySamePublication')}</button></>}
      {state.phase === 'conflict' && <p>{t('taskConflictHelp')}</p>}
      {!pending && <button className="text-button" type="button" onClick={onClose}>{t('close')}</button>}
    </div>}
    {error && <p className="form-error" role="alert">{t(error === 'artifact_candidate_mismatch' ? 'candidateMismatch' : 'prepareError')}</p>}
    {!pending && state.phase !== 'confirmed' && <ArtifactDraft key={previous.id} agentId={agentId} conversationId={conversationId} previous={previous} seed={state.candidate} blocked={blocked || reading} onClose={onClose} onConfirm={(candidate) => { if (blocked) return; publisher.current?.choose(candidate, previous); void publisher.current?.send() }} />}
  </section>
}
