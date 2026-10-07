import { useTranslation } from 'react-i18next'
import { useState } from 'react'
import type { PairedHost } from '../../api/environments'
import { pairedHostStatus } from './hosts'
import { HostProvisioners } from './HostProvisioners'

export function HostCard({ host, now, disabled, unverified, onChoose }: { host: PairedHost; now: number; disabled: boolean; unverified: boolean; onChoose: (host: PairedHost, operation: 'confirm' | 'revoke') => void }) {
  const { t, i18n } = useTranslation('environments')
  const { t: provisioners } = useTranslation('provisioners')
  const [openedHost, setOpenedHost] = useState<string | null>(null)
  const hostIdentity = `${host.installation_id}:${host.host_id}`
  const viewingProvisioners = openedHost === hostIdentity
  const state = unverified && host.status === 'active' ? 'offline' : pairedHostStatus(host, now)
  return <article className="host-card">
    <div className="computer-card-heading"><h4>{t('hostLabel', { code: host.fingerprint })}</h4><span className="computer-status">{t(`pairStatus_${state}`)}</span></div>
    <dl className="host-identification"><div><dt>{t('comparisonCode')}</dt><dd><code>{host.fingerprint}</code></dd></div></dl>
    {host.status === 'pending' && <><p className="quiet-note">{t('compareHelp')}</p><p className="quiet-note">{t('pairExpires', { date: new Date(host.pairing_expires_at).toLocaleString(i18n.resolvedLanguage) })}</p></>}
    {host.diagnostic && <><p className="quiet-note">{t('hostLastSeen', { date: new Date(host.last_seen!).toLocaleString(i18n.resolvedLanguage) })}</p><p className="host-driver">{t(`driver_${host.diagnostic.driver}`)} · {t(`host_${host.diagnostic.status}`)}</p>
      <dl className={`host-checks ${state === 'offline' ? 'stale' : ''}`} aria-label={t('hostCapabilities')}>{Object.entries(host.diagnostic.probe).map(([check, available]) => <div key={check}><dt>{t(`hostCheck_${check}`)}</dt><dd>{t(available ? 'checkYes' : 'checkNo')}</dd></div>)}</dl>
    </>}
    {host.status === 'active' && !host.diagnostic && <p className="quiet-note">{t('hostNoReport')}</p>}
    {state === 'offline' && <p className="quiet-note">{t('hostReportStale')}</p>}
    <div className="tool-actions">{state === 'pending' && <button className="button primary" type="button" disabled={disabled} onClick={() => onChoose(host, 'confirm')}>{t('pair')}</button>}{host.status !== 'revoked' && <button className="button secondary" type="button" disabled={disabled} onClick={() => onChoose(host, 'revoke')}>{t('hostRevoke')}</button>}</div>
    <div className="computer-card-heading"><h4>{provisioners('heading')}</h4><button type="button" className="text-button" aria-expanded={viewingProvisioners} aria-controls={viewingProvisioners ? `host-provisioners-${host.host_id}` : undefined} disabled={!viewingProvisioners && (disabled || host.status === 'pending')} onClick={() => setOpenedHost(viewingProvisioners ? null : hostIdentity)}>{provisioners(viewingProvisioners ? 'close' : 'open')}</button></div>
    {viewingProvisioners && <HostProvisioners key={hostIdentity} host={host} disabled={disabled} unverified={unverified} />}
  </article>
}
