import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { PairedHost } from '../../api/environments'
import { pairedHostStatus } from './hosts'

export interface HostChange { host: PairedHost; operation: 'confirm' | 'revoke'; requestId: string }
export function HostConfirmation({ change, now, busy, blocked, onConfirm, onCancel }: { change: HostChange; now: number; busy: boolean; blocked: boolean; onConfirm: () => void; onCancel: () => void }) {
  const { t } = useTranslation('environments')
  const heading = useRef<HTMLHeadingElement>(null); const [compared, setCompared] = useState(false)
  useEffect(() => { heading.current?.focus() }, [])
  const pair = change.operation === 'confirm'
  const expired = pair && pairedHostStatus(change.host, now) !== 'pending'
  return <section className="tool-confirmation host-confirmation" aria-labelledby="host-confirmation-heading">
    <h4 id="host-confirmation-heading" ref={heading} tabIndex={-1}>{t(pair ? 'pairConfirm' : 'hostRevokeConfirm')}</h4>
    <p>{t(pair ? 'pairImpact' : 'hostRevokeImpact', { name: t('hostLabel', { code: change.host.fingerprint }) })}</p>
    <p className="host-code"><strong>{t('comparisonCode')}</strong><code>{change.host.fingerprint}</code></p>
    {pair && <label className="checkbox-row"><input type="checkbox" checked={compared} disabled={busy || blocked || expired} onChange={(event) => setCompared(event.target.checked)} /><span>{t('pairCompared')}</span></label>}
    {expired && <p className="form-error" role="alert">{t('pairStatus_expired')}</p>}
    <div className="tool-actions"><button className="button secondary" type="button" disabled={busy} onClick={onCancel}>{t(pair ? 'pairBack' : 'hostKeep')}</button><button className="button primary" type="button" disabled={busy || blocked || expired || pair && !compared} onClick={onConfirm}>{t(busy ? 'saving' : pair ? 'pairConfirm' : 'hostRevokeConfirm')}</button></div>
  </section>
}
