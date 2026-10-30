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
  const load = useCallback(async (signal?: AbortSignal) => {
    try { setState(await getComputers(agentId, signal)) } catch (failure) { if (!signal?.aborted) setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void load(controller.signal) }, 4000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [load])
  const computer: Computer | undefined = state?.computers.find((item) => item.status !== 'removed')

  async function start() {
    setBusy(true); setError(null)
    request.current ??= crypto.randomUUID()
    try { await createComputer(agentId, request.current); request.current = null; await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  async function control(holder: 'agent' | 'human') {
    if (!computer) return
    setBusy(true); setError(null)
    try { await setComputerControl(computer, holder); await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  async function restart() {
    if (!computer) return
    setConfirming(false); setRestarting(true); setBusy(true); setError(null)
    // Mesmo identificador ao repetir após demora: a API devolve o mesmo reinício, sem outro.
    restartRequest.current ??= crypto.randomUUID()
    // Tela nova após o reinício: o visualizador reconecta sozinho.
    try { await restartComputer(computer, restartRequest.current); restartRequest.current = null; setScreen((value) => value + 1); await load() } catch (failure) {
      const problem = asApiError(failure)
      if (!['computer_restart_timeout', 'mutation_timeout', 'mutation_network'].includes(problem.code)) restartRequest.current = null
      setError(problem)
    } finally { setRestarting(false); setBusy(false) }
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
