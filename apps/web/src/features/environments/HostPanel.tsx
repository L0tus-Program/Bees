import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { confirmHostPair, revokeHostPair } from '../../api/environments'
import type { PairedHost } from '../../api/environments'
import { ErrorNotice } from '../../components/Feedback'
import { HostCard } from './HostCard'
import { HostConfirmation } from './HostConfirmation'
import type { HostChange } from './HostConfirmation'
import { loadPairedHosts, pairedHostStatus, sameHostAuthority } from './hosts'
import { uncertainEnvironmentMutation } from './state'

export function HostPanel({ onChanged }: { onChanged: () => void }) {
  const { t, i18n } = useTranslation('environments')
  const [hosts, setHosts] = useState<PairedHost[] | null>(null); const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false); const [error, setError] = useState<ApiError | null>(null); const [notice, setNotice] = useState<string | null>(null)
  const [change, setChange] = useState<HostChange | null>(null); const [now, setNow] = useState(Date.now)
  const read = useRef<AbortController | null>(null); const mutation = useRef<AbortController | null>(null); const readSequence = useRef(0)
  const readHosts = useCallback(async (showLoading = false, reconcile?: string) => {
    read.current?.abort(); const controller = new AbortController(); read.current = controller; const sequence = ++readSequence.current
    if (showLoading) setLoading(true)
    try {
      const result = await loadPairedHosts(controller.signal)
      if (!controller.signal.aborted && sequence === readSequence.current) {
        setHosts(result); setError(null); setLoading(false)
        setChange((current) => {
          if (!current) return null
          const updated = result.find((host) => host.host_id === current.host.host_id)
          return updated && sameHostAuthority(current.host, updated) ? { ...current, host: updated } : null
        })
        if (reconcile) setNotice(reconcile)
      }
    } catch (failure) { if (!controller.signal.aborted && sequence === readSequence.current) { setError(asApiError(failure)); setLoading(false); setChange(null) } }
  }, [])
  useEffect(() => {
    void readHosts(true)
    return () => { read.current?.abort(); mutation.current?.abort() }
  }, [readHosts])
  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    const poll = window.setInterval(() => { if (!busy && !document.hidden) void readHosts() }, 10_000)
    return () => { window.clearInterval(clock); window.clearInterval(poll) }
  }, [busy, readHosts])
  function choose(host: PairedHost, operation: 'confirm' | 'revoke') {
    if (busy || loading || error || operation === 'confirm' && pairedHostStatus(host) !== 'pending') return
    setNotice(null); setChange({ host, operation, requestId: crypto.randomUUID() })
  }
  async function confirm() {
    if (!change || busy || loading || error || change.operation === 'confirm' && pairedHostStatus(change.host) !== 'pending') return
    const current = change; const controller = new AbortController(); mutation.current = controller; read.current?.abort()
    setBusy(true); setNotice(null)
    try {
      await (current.operation === 'confirm' ? confirmHostPair(current.host, current.requestId, controller.signal) : revokeHostPair(current.host, current.requestId, controller.signal))
      if (!controller.signal.aborted) {
        setChange(null); setNotice(current.operation === 'confirm' ? 'pairSaved' : 'hostRevoked')
        await readHosts(true); onChanged()
      }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const issue = asApiError(failure)
        if (uncertainEnvironmentMutation(issue) || issue.status === 409) {
          setChange(null); setNotice(uncertainEnvironmentMutation(issue) ? 'hostUncertain' : 'hostConflict')
          await readHosts(true, uncertainEnvironmentMutation(issue) ? 'hostReconciled' : 'hostConflict'); onChanged()
        } else { setError(issue); setChange(null) }
      }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  const knownError = error && i18n.exists(`error_${error.code}`, { ns: 'environments' })
  return <section className="host-panel" aria-labelledby="paired-hosts-heading" aria-busy={busy || loading}>
    <div className="form-heading"><h3 id="paired-hosts-heading">{t('pairedHosts')}</h3><button className="button secondary" type="button" disabled={loading || busy} onClick={() => { setChange(null); void readHosts(true) }}>{t('hostsRefresh')}</button></div>
    <p className="form-introduction">{t('pairedHostsDescription')}</p><p className="transfer-notice">{t('pairedHostsScope')}</p><p className="quiet-note">{t('hostLauncher')}</p>
    {notice && <p className={['hostUncertain', 'hostConflict'].includes(notice) ? 'form-error' : 'form-success'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('hostsLoading')}</p>}
    {error && <>{knownError ? <p className="form-error" role="alert">{t(`error_${error.code}`)}</p> : <ErrorNotice error={error} />}<p className="quiet-note">{t('hostReadStale')}</p></>}
    {hosts?.length === 0 && <p className="tool-empty">{t('hostsEmpty')}</p>}
    {change && <HostConfirmation key={change.requestId} change={change} now={now} busy={busy} blocked={loading || error !== null} onConfirm={() => { void confirm() }} onCancel={() => setChange(null)} />}
    <div className="computer-list">{hosts?.map((host) => <HostCard key={host.host_id} host={host} now={now} disabled={busy || loading || error !== null || change !== null} unverified={error !== null} onChoose={choose} />)}</div>
  </section>
}
