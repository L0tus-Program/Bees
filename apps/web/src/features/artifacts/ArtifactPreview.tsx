import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { previewArtifact } from './api'
import type { ArtifactPreview as Preview, ArtifactRecord } from './api'

export function ArtifactPreview({ artifact, onClose }: { artifact: ArtifactRecord; onClose: () => void }) {
  const { t } = useTranslation('artifacts')
  const headingId = useId()
  const [state, setState] = useState<{ data: Preview | null; error: ApiError | null }>({ data: null, error: null })
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    const controller = new AbortController(); heading.current?.focus()
    if (artifact.conversation_id) void previewArtifact(artifact.agent_id, artifact.conversation_id, artifact, controller.signal).then((data) => { if (!controller.signal.aborted) setState({ data, error: null }) }, (error) => { if (!controller.signal.aborted) setState({ data: null, error: asApiError(error) }) })
    return () => controller.abort()
  }, [artifact])
  return <section className="artifact-preview" aria-labelledby={headingId}>
    <div className="artifact-panel-heading"><h4 ref={heading} tabIndex={-1} id={headingId}>{t('previewHeading', { name: artifact.name, version: artifact.version })}</h4><button type="button" className="text-button" onClick={onClose}>{t('close')}</button></div>
    <p className="quiet-note">{t('previewHelp')}</p>
    {!state.data && !state.error && <p role="status">{t('loading')}</p>}
    {state.error && <p className="form-error" role="alert">{t('previewError')}</p>}
    {state.data && <>{state.data.truncated && <p className="transfer-notice" role="status">{t('previewTruncated', { bytes: state.data.total_bytes })}</p>}<pre tabIndex={0} className="artifact-plain-preview">{state.data.text}</pre></>}
  </section>
}
