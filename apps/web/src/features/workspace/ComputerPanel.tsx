import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Hand, Loader2, Monitor, Power } from 'lucide-react'
import { createComputer, getComputers, setComputerControl } from '../../api/computers'
import type { Computer, ComputerState } from '../../api/computers'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Feedback'
import { ScreenViewer } from '../computer/ScreenViewer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

/** Tela do computador da abelha ao lado da conversa, com revezamento de controle. */
export function ComputerPanel({ agentId, name }: { agentId: string; name: string }) {
  const { t } = useTranslation('workspace')
  const [state, setState] = useState<ComputerState | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const request = useRef<string | null>(null)
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
      <div className="legacy-scope min-h-0 flex-1 overflow-hidden rounded-xl border bg-black"><ScreenViewer computer={computer} /></div>
      <Button variant={computer.control_holder === 'human' ? 'default' : 'outline'} disabled={busy} onClick={() => { void control(computer.control_holder === 'human' ? 'agent' : 'human') }}>
        <Hand />{t(computer.control_holder === 'human' ? 'returnControl' : 'takeControl')}
      </Button>
    </>}
  </div>
}
