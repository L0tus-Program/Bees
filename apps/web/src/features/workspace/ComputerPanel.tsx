import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Hand, Loader2, Monitor, Power, RotateCcw } from 'lucide-react'
import { createComputer, getComputers, restartComputer, setComputerControl } from '../../api/computers'
import type { Computer, ComputerState } from '../../api/computers'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Feedback'
import { ScreenViewer } from '../computer/ScreenViewer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

/** Tela do computador da abelha ao lado da conversa, com revezamento de controle e reinício. */
export function ComputerPanel({ agentId, name }: { agentId: string; name: string }) {
  const { t } = useTranslation('workspace')
  const [state, setState] = useState<ComputerState | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const request = useRef<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const restartRequest = useRef<string | null>(null)
  const [screen, setScreen] = useState(0)
  const reading = useRef<AbortController | null>(null)
  const mutating = useRef(false)
  const active = useRef(true)
  const load = useCallback(async () => {
    if (!active.current) return
    reading.current?.abort()
    const controller = new AbortController(); reading.current = controller
    try {
      const next = await getComputers(agentId, controller.signal)
      if (!controller.signal.aborted) setState(next)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (reading.current === controller) reading.current = null }
  }, [agentId])
  useEffect(() => {
    active.current = true
    void load()
    const timer = setInterval(() => { if (!mutating.current && !reading.current && document.visibilityState === 'visible') void load() }, 4000)
    return () => { active.current = false; reading.current?.abort(); clearInterval(timer) }
  }, [load])
  const computer: Computer | undefined = state?.computers.find((item) => item.status !== 'removed')

  function beginMutation() {
    if (mutating.current || !active.current) return false
    mutating.current = true
    // Nenhuma leitura iniciada antes da ação pode restaurar o estado anterior.
    reading.current?.abort()
    setBusy(true); setError(null)
    return true
  }
  function endMutation() {
    mutating.current = false
    if (active.current) setBusy(false)
  }

  async function start() {
    if (!beginMutation()) return
    request.current ??= crypto.randomUUID()
    try { await createComputer(agentId, request.current); request.current = null; await load() } catch (failure) { if (active.current) setError(asApiError(failure)) } finally { endMutation() }
  }
  async function control(holder: 'agent' | 'human') {
    if (!computer || !beginMutation()) return
    try { await setComputerControl(computer, holder); await load() } catch (failure) { if (active.current) setError(asApiError(failure)) } finally { endMutation() }
  }
  async function restart() {
    if (!computer || !beginMutation()) return
    setConfirming(false); setRestarting(true)
    // Mesmo identificador ao repetir após demora: a API devolve o mesmo reinício, sem outro.
    restartRequest.current ??= crypto.randomUUID()
    // Tela nova após o reinício: o visualizador reconecta sozinho.
    try { await restartComputer(computer, restartRequest.current); restartRequest.current = null; if (active.current) setScreen((value) => value + 1); await load() } catch (failure) {
      const problem = asApiError(failure)
      if (!['computer_restart_timeout', 'mutation_timeout', 'mutation_network'].includes(problem.code)) restartRequest.current = null
      if (active.current) setError(problem)
    } finally { if (active.current) setRestarting(false); endMutation() }
  }

  return <div className="flex h-full min-h-0 flex-col gap-3">
    <div className="flex items-center gap-2">
      <Monitor className="size-4 text-muted-foreground" aria-hidden="true" />
      <h2 className="flex-1 text-sm font-semibold">{t('computerTitle', { name })}</h2>
      {computer?.status === 'ready' && <Badge variant={computer.online ? 'secondary' : 'outline'}>{computer.online ? t('inUse') : '—'}</Badge>}
    </div>
    {error && <div className="legacy-scope"><ErrorNotice error={error} /></div>}
    {!state && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden="true" />{t('computerStarting')}</p>}
    {state && !computer && (state.runtime_paired && state.runtime_online
      ? <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed p-6"><p className="text-sm text-muted-foreground">{t('computerOff')}</p><Button onClick={() => { void start() }} disabled={busy}>{busy ? <Loader2 className="animate-spin" /> : <Power />}{t('computerStart')}</Button></div>
      : <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">{t('computerNoRuntime')}</p>)}
    {computer?.status === 'provisioning' && <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status"><Loader2 className="size-4 animate-spin" aria-hidden="true" />{t('computerStarting')}</p>}
    {computer?.status === 'failed' && <p className="text-sm text-destructive">{t('computerFailed')}</p>}
    {computer?.status === 'ready' && <>
      <div className="legacy-scope min-h-0 flex-1 overflow-hidden rounded-xl border bg-black"><ScreenViewer key={screen} computer={computer} /></div>
      {confirming
        ? <div className="flex flex-col gap-2 rounded-xl border p-3" role="group" aria-label={t('restart')}>
          <p className="text-sm">{t('restartConfirm')}</p>
          <div className="flex gap-2">
            <Button variant="destructive" onClick={() => { void restart() }}><RotateCcw />{t('restartConfirmAction')}</Button>
            <Button variant="outline" onClick={() => { setConfirming(false) }}>{t('cancel')}</Button>
          </div>
        </div>
        : <div className="flex gap-2">
          <Button className="flex-1" variant={computer.control_holder === 'human' ? 'default' : 'outline'} disabled={busy} onClick={() => { void control(computer.control_holder === 'human' ? 'agent' : 'human') }}>
            <Hand />{t(computer.control_holder === 'human' ? 'returnControl' : 'takeControl')}
          </Button>
          <Button variant="outline" disabled={busy || !computer.online} onClick={() => { setConfirming(true) }}>
            {restarting ? <Loader2 className="animate-spin" aria-hidden="true" /> : <RotateCcw />}{t(restarting ? 'restarting' : 'restart')}
          </Button>
        </div>}
    </>}
  </div>
}
