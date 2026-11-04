import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createComputer, createInvite, getComputers, removeComputer, restartComputer, setComputerControl } from '../../api/computers'
import type { Computer, ComputerState } from '../../api/computers'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { ScreenViewer } from './ScreenViewer'
import { PersonalConnectors } from './PersonalConnectors'
import { confirmAction } from '../../components/confirmAction'

export function ComputerWorkspace({ agentId }: { agentId: string }) {
  const { t, i18n } = useTranslation('computer')
  const [state, setState] = useState<ComputerState | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [readError, setReadError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [viewing, setViewing] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const createId = useRef<string | null>(null)
  const restartId = useRef<{ target: string; id: string } | null>(null)
  const [screen, setScreen] = useState(0)
  const reading = useRef<AbortController | null>(null)
  const mutating = useRef(false)
  const confirming = useRef(false)
  const active = useRef(true)
  const load = useCallback(async () => {
    if (!active.current) return
    reading.current?.abort()
    const controller = new AbortController(); reading.current = controller
    try {
      const next = await getComputers(agentId, controller.signal)
      if (!controller.signal.aborted) { setState(next); setReadError(null) }
    } catch (failure) { if (!controller.signal.aborted) setReadError(asApiError(failure)) }
    finally { if (reading.current === controller) reading.current = null }
  }, [agentId])
  useEffect(() => {
    active.current = true
    void load()
    const timer = setInterval(() => { if (!mutating.current && !reading.current && document.visibilityState === 'visible') void load() }, 4000)
    return () => { active.current = false; reading.current?.abort(); clearInterval(timer) }
  }, [load])
  async function mutate(operation: () => Promise<unknown>, success?: string) {
    if (mutating.current || !active.current) return
    mutating.current = true
    // Não aceitar snapshots iniciados antes da ação, mesmo que o fetch ignore abort.
    reading.current?.abort()
    setBusy(true); setError(null); setReadError(null); setNotice(null)
    try { await operation(); if (active.current && success) setNotice(success) }
    catch (failure) { if (active.current) setError(asApiError(failure)) }
    finally {
      await load()
      mutating.current = false
      if (active.current) setBusy(false)
    }
  }
  async function confirmMutation(message: string, operation: () => Promise<unknown>, success: string) {
    if (confirming.current || mutating.current || !active.current) return
    confirming.current = true
    try { if (await confirmAction(message)) await mutate(operation, success) }
    finally { confirming.current = false }
  }
  async function restart(computer: Computer) {
    const target = JSON.stringify([computer.agent_id, computer.id, computer.executor_id])
    if (restartId.current?.target !== target) restartId.current = { target, id: crypto.randomUUID() }
    try {
      await restartComputer(computer, restartId.current.id)
      restartId.current = null
      if (active.current) setScreen((value) => value + 1)
    } catch (failure) {
      const problem = asApiError(failure)
      // Só recibos terminais do runtime encerram o pedido. Uma recusa antes do
      // despacho (por exemplo offline) não resolve um reinício anterior incerto.
      if (problem.status === 409 && ['computer_restart_failed', 'computer_not_provisioned'].includes(problem.code)) restartId.current = null
      throw problem
    }
  }
  const computer: Computer | undefined = state?.computers.find((item) => item.status !== 'removed')
  return <section className="surface computer-workspace" aria-labelledby="computer-heading">
    <p className="section-label">{t('label')}</p>
    <h2 id="computer-heading">{t('title')}</h2>
    <p className="quiet-note">{t('scope')}</p>
    {error && <ErrorNotice error={error} />}
    {readError && <ErrorNotice error={readError} />}
    {notice && <p className="success-notice" role="status">{t(notice)}</p>}
    {!state && !error && !readError && <LoadingNotice />}
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
        <p role="status">{t(`status_${computer.status}`)}{computer.reason_code ? ` · ${i18n.exists(`reason_${computer.reason_code}`, { ns: 'computer' }) ? t(`reason_${computer.reason_code}`) : t('reason_other', { code: computer.reason_code })}` : ''}{computer.status === 'ready' && !computer.online ? ` · ${t('offline')}` : ''}</p>
        {computer.status === 'ready' && <>
          <p className="quiet-note">{t(computer.control_holder === 'human' ? 'humanControlHelp' : 'agentControlHelp')}</p>
          <div className="computer-actions">
            <button type="button" className="button secondary" disabled={!computer.online} aria-pressed={viewing} onClick={() => setViewing((value) => !value)}>{t(viewing ? 'hideScreen' : 'showScreen')}</button>
            {computer.control_holder === 'agent'
              ? <button type="button" className="button primary" disabled={busy} onClick={() => { void mutate(() => setComputerControl(computer, 'human'), 'controlTaken'); setViewing(true) }}>{t('takeControl')}</button>
              : <button type="button" className="button primary" disabled={busy} onClick={() => { void mutate(() => setComputerControl(computer, 'agent'), 'controlReturned') }}>{t('returnControl')}</button>}
            <button type="button" className="button secondary" disabled={busy || !computer.online} onClick={() => { void confirmMutation(t('restartConfirm'), () => restart(computer), 'restarted') }}>{t('restart')}</button>
            <button type="button" className="button secondary" disabled={busy} onClick={() => { void confirmMutation(t('removeConfirm'), () => removeComputer(computer), 'removed') }}>{t('remove')}</button>
          </div>
          {viewing && computer.online && <ScreenViewer key={screen} computer={computer} />}
        </>}
        {computer.status === 'failed' && <button type="button" className="button secondary" disabled={busy} onClick={() => { void mutate(() => removeComputer(computer), 'removed') }}>{t('remove')}</button>}
      </div>}
    </>}
    <PersonalConnectors agentId={agentId} />
  </section>
}
