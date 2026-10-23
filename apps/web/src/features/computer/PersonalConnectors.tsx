import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createInvite, getExecutors, getGrants, revokeExecutor, setGrant } from '../../api/computers'
import type { Executor, ExecutorGrant, Invite } from '../../api/computers'
import { ErrorNotice } from '../../components/Feedback'

function ConnectorGrant({ agentId, executor, onChanged }: { agentId: string; executor: Executor; onChanged: () => void }) {
  const { t } = useTranslation('computer')
  const [grant, setGrantState] = useState<ExecutorGrant | null | undefined>(undefined)
  const [draft, setDraft] = useState({ enabled: false, roots: [] as string[], write: false, shell: false, browser: false })
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try {
      const found = (await getGrants(executor.id)).find((item) => item.agent_id === agentId) ?? null
      setGrantState(found)
      if (found) setDraft({ enabled: found.enabled, ...found.scope })
    } catch (failure) { setError(asApiError(failure)) }
  }, [agentId, executor.id])
  useEffect(() => { void load() }, [load])
  async function save() {
    setBusy(true); setError(null)
    try { await setGrant(executor.id, agentId, { expected_revision: grant?.revision ?? 0, ...draft }); await load(); onChanged() } catch (failure) { setError(asApiError(failure)); await load() } finally { setBusy(false) }
  }
  const roots = executor.roots ?? []
  return <div className="connector-grant">
    {error && <ErrorNotice error={error} />}
    <label className="checkbox-field"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>{t('grantEnabled')}</span></label>
    <fieldset><legend>{t('grantRoots')}</legend>
      {roots.length === 0 && <small>{t('noRoots')}</small>}
      {roots.map((root) => <label key={root} className="checkbox-field"><input type="checkbox" checked={draft.roots.includes(root)} onChange={(event) => setDraft({ ...draft, roots: event.target.checked ? [...draft.roots, root] : draft.roots.filter((item) => item !== root) })} /><span>{root}</span></label>)}
    </fieldset>
    {(['write', 'shell', 'browser'] as const).map((key) => <label key={key} className="checkbox-field"><input type="checkbox" checked={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} /><span>{t(`grant_${key}`)}<small>{t(`grant_${key}_help`)}</small></span></label>)}
    <button type="button" className="button primary" disabled={busy || grant === undefined} onClick={() => { void save() }}>{t('saveGrant')}</button>
  </div>
}

export function PersonalConnectors({ agentId }: { agentId: string }) {
  const { t } = useTranslation('computer')
  const [items, setItems] = useState<Executor[]>([])
  const [invite, setInvite] = useState<Invite | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { setItems((await getExecutors('personal')).filter((item) => item.status === 'active')) } catch (failure) { setError(asApiError(failure)) }
  }, [])
  useEffect(() => { void load(); const timer = setInterval(() => { void load() }, 5000); return () => clearInterval(timer) }, [load])
  async function pair() {
    setBusy(true); setError(null)
    try { setInvite(await createInvite('personal', t('defaultConnectorName'))) } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  async function revoke(executor: Executor) {
    if (!window.confirm(t('revokeConfirm'))) return
    setBusy(true)
    try { await revokeExecutor(executor); await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  return <section className="personal-connectors" aria-labelledby="personal-heading">
    <h3 id="personal-heading">{t('personalTitle')}</h3>
    <p className="quiet-note">{t('personalScope')}</p>
    {error && <ErrorNotice error={error} />}
    {items.map((executor) => <article key={executor.id} className="connector-card">
      <h4>{executor.name} <small className={executor.online ? 'status-online' : 'status-offline'}>{t(executor.online ? 'online' : 'offline_label')}</small></h4>
      {executor.platform && <small>{executor.platform}</small>}
      <ConnectorGrant agentId={agentId} executor={executor} onChanged={() => { void load() }} />
      <button type="button" className="text-button" disabled={busy} onClick={() => { void revoke(executor) }}>{t('revoke')}</button>
    </article>)}
    <button type="button" className="button secondary" disabled={busy} onClick={() => { void pair() }}>{t('pairPersonal')}</button>
    {invite && <div className="connector-invite" role="status">
      <p>{t('inviteInstructions')}</p>
      <code className="invite-token">{invite.token}</code>
      <pre>{`bees-executor personal --api ${window.location.origin} --invite ${invite.token} --root Documentos=~/Documents`}</pre>
      <small>{t('inviteExpires', { time: new Date(invite.expires_at).toLocaleTimeString() })}</small>
    </div>}
  </section>
}
