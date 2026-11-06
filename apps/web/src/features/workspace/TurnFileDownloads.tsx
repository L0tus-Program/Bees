import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { FileText } from 'lucide-react'
import { asApiError } from '../../api/client'
import { downloadArtifact } from '../artifacts/api'
import type { ArtifactRecord } from '../artifacts/api'

function size(bytes: number, language: string) {
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes; let unit = 0
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1 }
  return `${new Intl.NumberFormat(language, { maximumFractionDigits: unit ? 1 : 0 }).format(value)} ${units[unit]}`
}

/** Remounted for each task snapshot: no download can outlive its visible files. */
export function TurnFileDownloads({ agentId, taskId, files }: { agentId: string; taskId: string; files: ArtifactRecord[] }) {
  const { t, i18n } = useTranslation('workspace')
  const { t: artifactText } = useTranslation('artifacts')
  const [state, setState] = useState<{ busy: boolean; error?: string; sent?: boolean }>({ busy: false })
  const operation = useRef<AbortController | null>(null)
  const releases = useRef(new Set<() => void>())
  useEffect(() => {
    const pending = releases.current
    return () => { operation.current?.abort(); pending.forEach((release) => release()); pending.clear() }
  }, [])
  async function download(file: ArtifactRecord) {
    // A ref closes the same-event gap before React commits disabled controls.
    if (operation.current) return
    const controller = new AbortController(); operation.current = controller
    setState({ busy: true })
    try {
      const blob = await downloadArtifact(agentId, taskId, file, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      let timer: ReturnType<typeof setTimeout> | undefined
      const release = () => { clearTimeout(timer); URL.revokeObjectURL(url); releases.current.delete(release) }
      releases.current.add(release)
      try {
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.name
        try { document.body.append(anchor); anchor.click() } finally { anchor.remove() }
        timer = setTimeout(release, 30000)
      } catch (failure) { release(); throw failure }
      setState({ busy: false, sent: true })
    } catch (failure) {
      if (!controller.signal.aborted) {
        const code = asApiError(failure).code
        const error = code === 'artifact_integrity_failed' || code === 'artifact_blob_missing' ? 'integrityError'
          : code === 'artifact_not_ready' ? 'notReadyError' : code === 'artifact_download_too_large' ? 'sizeError' : 'downloadError'
        setState({ busy: false, error })
      }
    } finally { if (operation.current === controller) operation.current = null }
  }
  return <div aria-busy={state.busy}>
    <ul className="mt-2 flex flex-wrap gap-2">
      {files.map((file) => <li key={file.id}>
        <button type="button" className="flex max-w-xs items-center gap-2 rounded-lg border bg-card px-3 py-2 text-left text-sm text-foreground shadow-xs hover:bg-muted disabled:opacity-50" disabled={state.busy} onClick={() => { void download(file) }} aria-label={t('download', { name: file.name })}>
          <FileText className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="min-w-0 truncate font-medium">{file.name}</span>
          <span className="shrink-0 text-xs text-muted-foreground">{size(file.size_bytes, i18n.resolvedLanguage ?? 'pt-BR')}</span>
        </button>
      </li>)}
    </ul>
    {state.busy && <p className="text-xs text-muted-foreground" role="status">{artifactText('downloading')}</p>}
    {state.sent && <p className="text-xs text-muted-foreground" role="status">{artifactText('sent')}</p>}
    {state.error && <p className="text-sm text-destructive" role="alert">{artifactText(state.error)}</p>}
  </div>
}
