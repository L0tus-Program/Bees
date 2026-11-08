import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createInvite, getExecutors, getGrants, revokeExecutor, setGrant } from '../../api/computers'
import type { Executor, ExecutorGrant, Invite } from '../../api/computers'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

function ConnectorGrant({ agentId, executor, onChanged }: { agentId: string; executor: Executor; onChanged: () => void }) {
  const { t } = useTranslation('computer')
  const [grant, setGrantState] = useState<ExecutorGrant | null | undefined>(undefined)
  const [draft, setDraft] = useState({ enabled: false, roots: [] as string[], write: false, shell: false, browser: false })
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<ApiError | null>(null)
  const lifecycle = useRef(0)
  const saving = useRef(false)
  const applyGrant = useCallback((found: ExecutorGrant | null) => {
    setGrantState(found)
    setDraft(found ? { enabled: found.enabled, ...found.scope } : { enabled: false, roots: [], write: false, shell: false, browser: false })
  }, [])
  const load = useCallback(async (generation: number) => {
    try {
      const found = (await getGrants(executor.id)).find((item) => item.agent_id === agentId) ?? null
      if (lifecycle.current !== generation) return
      applyGrant(found)
      setLoadError(null)
    } catch (failure) {
      if (lifecycle.current === generation) {
        setGrantState(undefined)
        setLoadError(asApiError(failure))
      }
    }
  }, [agentId, executor.id, applyGrant])
  useEffect(() => {
    const generation = ++lifecycle.current
    void load(generation)
    return () => { lifecycle.current = generation + 1 }
  }, [load])
  async function save() {
    if (saving.current || grant === undefined) return
    saving.current = true
    const generation = lifecycle.current
    setBusy(true); setError(null)
    try {
      const receipt = await setGrant(executor.id, agentId, { expected_revision: grant?.revision ?? 0, ...draft })
      if (lifecycle.current !== generation) return
      applyGrant(receipt)
      onChanged()
    } catch (failure) {
      if (lifecycle.current !== generation) return
      setError(asApiError(failure))
      await load(generation)
    } finally {
      if (lifecycle.current === generation) {
        saving.current = false
        setBusy(false)
      }
    }
  }
  const roots = executor.roots ?? []
  return <div className="connector-grant">
    {error && <ErrorNotice error={error} />}
    {loadError && <ErrorNotice error={loadError} />}
    <label className="checkbox-field"><input type="checkbox" disabled={busy || grant === undefined} checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>{t('grantEnabled')}</span></label>
    <fieldset><legend>{t('grantRoots')}</legend>
      {roots.length === 0 && <small>{t('noRoots')}</small>}
      {roots.map((root) => <label key={root} className="checkbox-field"><input type="checkbox" disabled={busy || grant === undefined} checked={draft.roots.includes(root)} onChange={(event) => setDraft({ ...draft, roots: event.target.checked ? [...draft.roots, root] : draft.roots.filter((item) => item !== root) })} /><span>{root}</span></label>)}
    </fieldset>
    {(['write', 'shell', 'browser'] as const).map((key) => <label key={key} className="checkbox-field"><input type="checkbox" disabled={busy || grant === undefined} checked={draft[key]} onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} /><span>{t(`grant_${key}`)}<small>{t(`grant_${key}_help`)}</small></span></label>)}
    <button type="button" className="button primary" disabled={busy || grant === undefined} onClick={() => { void save() }}>{t('saveGrant')}</button>
  </div>
}

export function PersonalConnectors({ agentId }: { agentId: string }) {
  const { t, i18n } = useTranslation('computer')
  const [items, setItems] = useState<Executor[]>([])
  const [invite, setInvite] = useState<Invite | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<ApiError | null>(null)
  const lifecycle = useRef(0)
  const reads = useRef(0)
  const mutating = useRef(false)
  const load = useCallback(async (afterMutation = false) => {
    if (mutating.current && !afterMutation) return
    const generation = lifecycle.current
    const request = ++reads.current
    try {
      const found = await getExecutors('personal')
      if (lifecycle.current !== generation || reads.current !== request) return
      setItems(found.filter((item) => item.status === 'active'))
      setLoadError(null)
    } catch (failure) {
      if (lifecycle.current === generation && reads.current === request) setLoadError(asApiError(failure))
    }
  }, [])
  useEffect(() => {
    const generation = ++lifecycle.current
    void load()
    const timer = setInterval(() => { void load() }, 5000)
    return () => { lifecycle.current = generation + 1; clearInterval(timer) }
  }, [load])
  async function pair() {
    if (mutating.current) return
    mutating.current = true
    ++reads.current
    const generation = lifecycle.current
    setBusy(true); setError(null); setInvite(null)
    try {
      const receipt = await createInvite('personal', t('defaultConnectorName'))
      if (lifecycle.current === generation) setInvite(receipt)
    } catch (failure) {
      if (lifecycle.current === generation) setError(asApiError(failure))
    } finally {
      if (lifecycle.current === generation) { mutating.current = false; setBusy(false) }
    }
  }
  async function revoke(executor: Executor) {
    if (mutating.current) return
    mutating.current = true
    ++reads.current
    const generation = lifecycle.current
    setBusy(true)
    try {
      if (!await confirmAction(t('revokeConfirm')) || lifecycle.current !== generation) return
      setError(null)
      const receipt = await revokeExecutor(executor)
      if (lifecycle.current !== generation) return
      setItems((current) => current.filter((item) => item.id !== receipt.id))
      await load(true)
    } catch (failure) {
      if (lifecycle.current === generation) setError(asApiError(failure))
    } finally {
      if (lifecycle.current === generation) { mutating.current = false; setBusy(false) }
    }
  }
  return <section className="personal-connectors" aria-labelledby="personal-heading">
    <h3 id="personal-heading">{t('personalTitle')}</h3>
    <p className="quiet-note">{t('personalScope')}</p>
    {error && <ErrorNotice error={error} />}
    {loadError && <ErrorNotice error={loadError} />}
    {items.map((executor) => <article key={executor.id} className="connector-card">
      <h4>{executor.name} <small className={executor.online ? 'status-online' : 'status-offline'}>{t(executor.online ? 'online' : 'offline_label')}</small></h4>
      {executor.platform && <small>{executor.platform}</small>}
      <ConnectorGrant key={JSON.stringify([agentId, executor.id])} agentId={agentId} executor={executor} onChanged={() => { void load() }} />
      <button type="button" className="text-button" disabled={busy} onClick={() => { void revoke(executor) }}>{t('revoke')}</button>
    </article>)}
    <button type="button" className="button secondary" disabled={busy} onClick={() => { void pair() }}>{t('pairPersonal')}</button>
    {invite && <div className="connector-invite" role="status">
      <p>{t('inviteInstructions')}</p>
      <code className="invite-token">{invite.token}</code>
      <pre>{`bees-executor personal --api ${window.location.origin} --invite=${invite.token} --root Documentos=~/Documents`}</pre>
      <small>{t('inviteExpires', { time: new Date(invite.expires_at).toLocaleTimeString(i18n.resolvedLanguage) })}</small>
    </div>}
  </section>
}
