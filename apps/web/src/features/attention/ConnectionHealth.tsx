import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { getConnectionHealth } from '../../api/connectionHealth'
import type { ConnectionIssue } from '../../api/connectionHealth'
import { ErrorNotice } from '../../components/Feedback'

export function ConnectionHealth({ agentId }: { agentId: string }) {
  const { t, i18n } = useTranslation('health')
  const [issues, setIssues] = useState<ConnectionIssue[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const load = useCallback(async (signal?: AbortSignal) => {
    setError(null)
    try { setIssues(await getConnectionHealth(agentId, signal)) } catch (failure) { if (!signal?.aborted) setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  const message = (issue: ConnectionIssue) => i18n.exists(`code_${issue.code}`, { ns: 'health' }) ? t(`code_${issue.code}`, { count: Number(issue.detail) || 0 }) : t('code_other')
  return <section className="connection-health" aria-labelledby="connection-health-title">
    <h3 id="connection-health-title">{t('title')}</h3>
    {error && <ErrorNotice error={error} />}
    <div aria-live="polite">
      {issues?.length === 0 && <p className="quiet-note">{t('none')}</p>}
      {!!issues?.length && <ul className="channel-list">{issues.map((issue) => <li key={`${issue.kind}:${issue.id}`} className="channel-card" role={issue.blocking ? 'alert' : undefined}>
        <div className="channel-heading"><strong>{issue.name}</strong><span className="provider-label">{t(`kind_${issue.kind}`)}</span></div>
        <p>{message(issue)}</p>
      </li>)}</ul>}
    </div>
    <button type="button" className="text-button" onClick={() => { void load() }}>{t('refresh')}</button>
  </section>
}
