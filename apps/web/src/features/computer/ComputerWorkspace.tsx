import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createComputer, createInvite, getComputers, removeComputer, setComputerControl } from '../../api/computers'
import type { Computer, ComputerState } from '../../api/computers'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { ScreenViewer } from './ScreenViewer'
import { PersonalConnectors } from './PersonalConnectors'

export function ComputerWorkspace({ agentId }: { agentId: string }) {
  const { t } = useTranslation('computer')
  const [state, setState] = useState<ComputerState | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const createId = useRef<string | null>(null)
  const load = useCallback(async (signal?: AbortSignal) => {
    try { setState(await getComputers(agentId, signal)); setError(null) } catch (failure) { if (!signal?.aborted) setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    const timer = setInterval(() => { void load(controller.signal) }, 4000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [load])
  async function mutate(operation: () => Promise<unknown>, success?: string) {
    if (busy) return
    setBusy(true); setError(null); setNotice(null)
    try { await operation(); if (success) setNotice(success); await load() } catch (failure) { setError(asApiError(failure)); await load() } finally { setBusy(false) }
  }
  const computer: Computer | undefined = state?.computers.find((item) => item.status !== 'removed')
  return <section className="surface computer-workspace" aria-labelledby="computer-heading">
    <p className="section-label">{t('label')}</p>
    <h2 id="computer-heading">{t('title')}</h2>
    <p className="quiet-note">{t('scope')}</p>
    {error && <ErrorNotice error={error} />}
    {notice && <p className="success-notice" role="status">{t(notice)}</p>}
    {!state && !error && <LoadingNotice />}
    {state && <>
      <div className="computer-runtime">
        <strong>{t('runtime')}</strong>
        <span className={state.runtime_online ? 'status-online' : 'status-offline'}>{t(state.runtime_online ? 'runtimeOnline' : state.runtime_paired ? 'runtimeOffline' : 'runtimeUnpaired')}</span>
        {!state.runtime_paired && <>
          <p className="quiet-note">{t(state.local_link_available ? 'pairLocalHelp' : 'pairManualHelp')}</p>
          <button type="button" className="button secondary" disabled={busy} onClick={() => { void mutate(() => createInvite('computer_runtime', 'Runtime de computadores'), state.local_link_available ? 'pairRequested' : 'inviteCreated') }}>{t('pairRuntime')}</button>
        </>}
      </div>
      {!computer && <div className="computer-empty">
        <p>{t('noComputer')}</p>
        <button type="button" className="button primary" disabled={busy || !state.runtime_online} onClick={() => { createId.current ??= crypto.randomUUID(); void mutate(async () => { await createComputer(agentId, createId.current!); createId.current = null }, 'created') }}>{t('create')}</button>
        {!state.runtime_online && <small>{t('createNeedsRuntime')}</small>}
      </div>}
      {computer && <div className="computer-card">
        <h3>{computer.name}</h3>
        <p role="status">{t(`status_${computer.status}`)}{computer.reason_code ? ` · ${computer.reason_code}` : ''}{computer.status === 'ready' && !computer.online ? ` · ${t('offline')}` : ''}</p>
        {computer.status === 'ready' && <>
          <p className="quiet-note">{t(computer.control_holder === 'human' ? 'humanControlHelp' : 'agentControlHelp')}</p>
          <div className="computer-actions">
            <button type="button" className="button secondary" disabled={!computer.online} aria-pressed={viewing} onClick={() => setViewing((value) => !value)}>{t(viewing ? 'hideScreen' : 'showScreen')}</button>
            {computer.control_holder === 'agent'
              ? <button type="button" className="button primary" disabled={busy} onClick={() => { void mutate(() => setComputerControl(computer, 'human'), 'controlTaken'); setViewing(true) }}>{t('takeControl')}</button>
              : <button type="button" className="button primary" disabled={busy} onClick={() => { void mutate(() => setComputerControl(computer, 'agent'), 'controlReturned') }}>{t('returnControl')}</button>}
            <button type="button" className="button secondary" disabled={busy} onClick={() => { if (window.confirm(t('removeConfirm'))) void mutate(() => removeComputer(computer), 'removed') }}>{t('remove')}</button>
          </div>
          {viewing && computer.online && <ScreenViewer key={`${computer.id}:${computer.control_revision}`} computer={computer} />}
        </>}
        {computer.status === 'failed' && <button type="button" className="button secondary" disabled={busy} onClick={() => { void mutate(() => removeComputer(computer), 'removed') }}>{t('remove')}</button>}
      </div>}
    </>}
    <PersonalConnectors agentId={agentId} />
  </section>
}
