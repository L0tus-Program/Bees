import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { PairedHost } from '../../api/environments'
import { getProvisioners, revokeProvisioner } from '../../api/provisioners'
import type { ProvisionerPage, ProvisionerRecord } from '../../api/provisioners'
import { checkedProvisionerSnapshot } from './provisionersState'

export function ProvisionerConfirmation({ record, busy, blocked, onConfirm, onCancel }: { record: ProvisionerRecord; busy: boolean; blocked: boolean; onConfirm: () => void; onCancel: () => void }) {
  const { t } = useTranslation('provisioners')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  return <section className="tool-confirmation" aria-labelledby={`revoke-provisioner-${record.provisioner_id}`}>
    <h3 id={`revoke-provisioner-${record.provisioner_id}`} ref={heading} tabIndex={-1}>{t('confirmation')}</h3>
    <p><code>{record.provisioner_id}</code></p><p>{t('impact')}</p>
    <div className="tool-actions"><button type="button" className="text-button" disabled={busy} onClick={onCancel}>{t('back')}</button><button type="button" className="button danger" disabled={busy || blocked} onClick={onConfirm}>{t(busy ? 'revoking' : 'confirm')}</button></div>
  </section>
}

export function HostProvisioners({ host, disabled, unverified }: { host: PairedHost; disabled: boolean; unverified: boolean }) {
  const { t } = useTranslation('provisioners')
  const [page, setPage] = useState<ProvisionerPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [verified, setVerified] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [intent, setIntent] = useState<{ snapshot: ProvisionerRecord; requestId: string } | null>(null)
  const known = useRef(new Map<string, ProvisionerRecord>())
  const reader = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const hostRef = useRef(host); hostRef.current = host
  const load = useCallback(async (offset = 0) => {
    reader.current?.abort()
    const operation = new AbortController(); reader.current = operation
    setLoading(true); setVerified(false)
    try {
      const incoming = await getProvisioners(hostRef.current, operation.signal, offset)
      if (operation.signal.aborted) return
      checkedProvisionerSnapshot(incoming.items, known.current)
      incoming.items.forEach((item) => known.current.set(item.provisioner_id, item))
      setPage((previous) => ({ ...incoming, items: offset > 0 && previous ? [...previous.items.filter((item) => !incoming.items.some((next) => next.provisioner_id === item.provisioner_id)), ...incoming.items] : incoming.items }))
      setVerified(true); setError(null)
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); setVerified(false) } }
    finally { if (!operation.signal.aborted) setLoading(false) }
  }, [])
  useEffect(() => { void load(); return () => { reader.current?.abort(); mutation.current?.abort() } }, [load])
  const blocked = disabled || unverified || host.status === 'pending' || !verified || loading || busy
  const current = intent && page?.items.find((item) => item.provisioner_id === intent.snapshot.provisioner_id)
  const staleIntent = !!intent && (!current || current.revision !== intent.snapshot.revision || current.status !== 'active')
  async function revoke() {
    if (!intent || blocked || staleIntent) return
    const operation = new AbortController(); mutation.current = operation
    setBusy(true); setError(null)
    try {
      const result = await revokeProvisioner(hostRef.current, intent.snapshot, intent.requestId, operation.signal)
      if (operation.signal.aborted) return
      checkedProvisionerSnapshot([result], known.current); known.current.set(result.provisioner_id, result)
      setPage((previous) => previous && ({ ...previous, items: previous.items.map((item) => item.provisioner_id === result.provisioner_id ? result : item) }))
      setIntent(null)
    } catch (failure) { if (!operation.signal.aborted) { setError(asApiError(failure)); setVerified(false) } }
    finally { if (!operation.signal.aborted) setBusy(false) }
  }
  return <section id={`host-provisioners-${host.host_id}`} className="tool-access" aria-label={t('heading')}>
    <p className="quiet-note">{t('scope')}</p>
    {loading && <p className="quiet-note" role="status">{t('loading')}</p>}
    {error && <p className="form-error" role="status">{t('readBeforeContinue')}</p>}
    <div className="tool-actions"><button type="button" className="text-button" disabled={busy || loading} onClick={() => { void load() }}>{t('refresh')}</button></div>
    {page && page.items.length === 0 && <p className="quiet-note">{t('empty')}</p>}
    {page?.items.map((item) => <article className="tool-card" key={item.provisioner_id}>
      <p><code>{item.provisioner_id}</code></p><p className="quiet-note">{t(`status_${item.status}`)}</p>
      {item.status === 'active' && <button type="button" className="button secondary" disabled={blocked || !!intent} onClick={() => setIntent({ snapshot: item, requestId: crypto.randomUUID() })}>{t('revoke')}</button>}
    </article>)}
    {page?.has_more && <button type="button" className="button secondary" disabled={loading || busy || !verified} onClick={() => { if (page.next_offset !== null) void load(page.next_offset) }}>{t('more')}</button>}
    {intent && <ProvisionerConfirmation record={intent.snapshot} busy={busy} blocked={blocked || staleIntent} onConfirm={() => { void revoke() }} onCancel={() => setIntent(null)} />}
    {staleIntent && <p className="transfer-notice">{t('changed')}</p>}
  </section>
}
