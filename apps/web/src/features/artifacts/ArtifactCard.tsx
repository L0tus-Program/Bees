import { useTranslation } from 'react-i18next'
import { useId } from 'react'
import type { ArtifactRecord } from './api'
import { TaskTime } from '../tasks/TaskTime'

export function ArtifactCard({ artifact, busy, blocked, onDownload, onPreview, onReview, selected, onSelect }: { artifact: ArtifactRecord; busy: boolean; blocked: boolean; onDownload: () => void; onPreview?: () => void; onReview?: () => void; selected?: boolean; onSelect?: () => void }) {
  const { t, i18n } = useTranslation('artifacts')
  const headingId = useId()
  const number = (value: number) => new Intl.NumberFormat(i18n.resolvedLanguage ?? i18n.language).format(value)
  const size = artifact.size_bytes >= 1024 * 1024 ? `${number(Number((artifact.size_bytes / (1024 * 1024)).toFixed(2)))} MiB` : artifact.size_bytes >= 1024 ? `${number(Number((artifact.size_bytes / 1024).toFixed(2)))} KiB` : t('bytes', { value: number(artifact.size_bytes) })
  return <article className="artifact-card" aria-labelledby={headingId}>
    <div className="artifact-card-heading"><h5 id={headingId}>{artifact.name}</h5><span className={`artifact-status artifact-status-${artifact.status}`}>{t(`status_${artifact.status}`)}</span></div>
    <dl className="artifact-summary"><div><dt>{t('format')}</dt><dd>{artifact.media_type}</dd></div><div><dt>{t('size')}</dt><dd title={t('bytes', { value: number(artifact.size_bytes) })}>{size}</dd></div><div><dt>{t('version')}</dt><dd>{number(artifact.version)}</dd></div></dl>
    <p className="quiet-note">{t('created')}: <TaskTime value={artifact.created_at} /></p>
    <p className="quiet-note">{t(`source_${artifact.source}`)} · {t('beesStorage')}</p>
    <details className="artifact-provenance"><summary>{t('provenance')}</summary><dl>
      <div><dt>{t('hash')}</dt><dd><code>{artifact.sha256 ?? t('noHash')}</code></dd></div>
      <div><dt>{t('conversation')}</dt><dd><code>{artifact.conversation_id ?? t('noConversation')}</code></dd></div>
      <div><dt>{t('task')}</dt><dd><code>{artifact.task_id ?? t('noTask')}</code></dd></div>
      <div><dt>{t('run')}</dt><dd><code>{artifact.run_id ?? t('noRun')}</code></dd></div>
      <div><dt>{t('artifact')}</dt><dd><code>{artifact.id}</code></dd></div>
      {artifact.series_id && <div><dt>{t('series')}</dt><dd><code>{artifact.series_id}</code></dd></div>}
      {artifact.previous_id && <div><dt>{t('previous')}</dt><dd><code>{artifact.previous_id}</code></dd></div>}
    </dl></details>
    {artifact.status === 'ready' ? <><div className="artifact-card-actions"><button className="button secondary" type="button" disabled={busy || blocked} aria-label={`${t('download', { version: artifact.version })}: ${artifact.name}`} onClick={onDownload}>{t(busy ? 'downloading' : 'download', { version: artifact.version })}</button>{onPreview && <button className="text-button" type="button" disabled={blocked} onClick={onPreview}>{t('preview')}</button>}{onReview && <button className="text-button" type="button" disabled={blocked} onClick={onReview}>{t('review')}</button>}</div>{onSelect && <label className="checkbox-field"><input type="checkbox" checked={selected === true} disabled={blocked} onChange={onSelect} /><span>{t('selectForTurn', { version: artifact.version })}</span></label>}</> : <p className="quiet-note">{t('unavailable')}</p>}
  </article>
}
