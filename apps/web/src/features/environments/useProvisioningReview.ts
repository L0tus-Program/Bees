import { useCallback, useEffect, useRef, useState } from 'react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { EnvironmentRecord } from '../../api/environments'
import { authorizeProvisioning, getProvisioning, prepareProvisioning, revokeProvisioning } from '../../api/provisioning'
import type { ProvisioningReviewState } from '../../api/provisioning'
import { canAuthorizePlan, canPreparePlan, canRevokePlan, provisioningAttemptIds, reviewKey } from './provisioningState'
import { reconcileProvisioningMutation } from './provisioningMutation'

export function useProvisioningReview(agentId: string, environment: EnvironmentRecord) {
  const [state, setState] = useState<ProvisioningReviewState | null>(null); const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false); const [error, setError] = useState<ApiError | null>(null); const [notice, setNotice] = useState<string | null>(null)
  const [selection, setSelection] = useState<{ action: 'authorize' | 'revoke'; key: string } | null>(null); const [compared, setCompared] = useState(false); const [now, setNow] = useState(Date.now)
  const read = useRef<AbortController | null>(null); const mutation = useRef<AbortController | null>(null); const sequence = useRef(0); const mutationLock = useRef(false)
  const identify = useRef(provisioningAttemptIds()); const context = `${agentId}:${environment.id}:${environment.revision}`; const currentContext = useRef(context); currentContext.current = context
  const load = useCallback(async (showLoading = false) => {
    read.current?.abort(); const controller = new AbortController(); read.current = controller; const revision = ++sequence.current; const scope = currentContext.current
    if (showLoading) setLoading(true)
    try {
      const value = await getProvisioning(agentId, environment.id, controller.signal)
      if (!controller.signal.aborted && sequence.current === revision && currentContext.current === scope) {
        setState(value); setLoading(false); setError(null)
        setSelection((previous) => previous && value.plan && reviewKey(value.plan) === previous.key ? previous : null)
      }
    } catch (failure) { if (!controller.signal.aborted && sequence.current === revision && currentContext.current === scope) { setError(asApiError(failure)); setLoading(false); setSelection(null); setCompared(false) } }
  }, [agentId, environment.id])
  useEffect(() => {
    setSelection(null); setCompared(false); mutation.current?.abort(); mutationLock.current = false; setBusy(false)
    void load(true)
    return () => { read.current?.abort(); mutation.current?.abort() }
  }, [load, environment.revision])
  useEffect(() => {
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    const poll = window.setInterval(() => { if (!mutationLock.current && !document.hidden) void load() }, 10_000)
    return () => { window.clearInterval(clock); window.clearInterval(poll) }
  }, [load])
  async function mutate(action: 'prepare' | 'authorize' | 'revoke') {
    if (!state || loading || error || mutationLock.current) return
    const plan = state.plan; const host = state.host
    if (action === 'prepare' ? !canPreparePlan(state, environment) : !plan || selection?.key !== reviewKey(plan)
      || (action === 'authorize' ? !compared || !canAuthorizePlan(plan, state, environment) : !canRevokePlan(plan))) return
    const scope = currentContext.current; const controller = new AbortController(); mutation.current = controller; mutationLock.current = true
    read.current?.abort(); setBusy(true); setNotice(null)
    const signature = action === 'prepare' ? JSON.stringify([action, scope, host!.host_id, host!.revision, plan?.plan_id ?? null]) : JSON.stringify([action, scope, plan!.plan_id, plan!.revision, plan!.plan_hash])
    const id = identify.current(signature)
    try {
      const result = await reconcileProvisioningMutation(action,
        () => action === 'prepare' ? prepareProvisioning(agentId, environment, host!, id, controller.signal)
          : action === 'authorize' ? authorizeProvisioning(agentId, environment.id, plan!, id, controller.signal) : revokeProvisioning(agentId, environment.id, plan!, id, controller.signal),
        () => getProvisioning(agentId, environment.id, controller.signal), controller.signal,
        (message) => { if (currentContext.current === scope) setNotice(message) })
      if (!controller.signal.aborted && currentContext.current === scope) {
        setSelection(null); setCompared(false)
        setState(result.state); setError(null); setNotice(result.notice)
      }
    } catch (failure) {
      if (!controller.signal.aborted && currentContext.current === scope) {
        setSelection(null); setCompared(false); setError(asApiError(failure))
      }
    } finally { if (mutation.current === controller) { mutationLock.current = false; if (!controller.signal.aborted && currentContext.current === scope) setBusy(false) } }
  }
  const choose = (action: 'authorize' | 'revoke') => {
    if (!state?.plan || loading || error || busy) return
    setCompared(false); setNotice(null); setSelection({ action, key: reviewKey(state.plan) })
  }
  return { state, loading, busy, error, notice, selection, compared, now, setCompared, choose, mutate,
    cancelSelection: () => { setSelection(null); setCompared(false) }, refresh: () => { setSelection(null); setCompared(false); void load(true) } }
}
