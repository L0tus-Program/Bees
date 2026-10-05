import { useCallback, useEffect, useRef, useState } from 'react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { startTaskPolling } from './polling'

// Read-only observation. Execution lives in the worker, never in this subscription.
export function useTaskPolling<T>(load: (signal: AbortSignal) => Promise<T>, merge?: (incoming: T, previous: T) => T) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const refreshRef = useRef<() => void>(() => {})
  const refresh = useCallback(() => refreshRef.current(), [])
  useEffect(() => {
    const visible = () => document.visibilityState === 'visible'
    const poller = startTaskPolling(load,
      (value) => { setData((previous) => previous && merge ? merge(value, previous) : value); setError(null); setLoading(false) },
      (failure) => { setError(asApiError(failure)); setLoading(false) }, visible)
    const becameVisible = () => { if (visible()) poller.refresh() }
    refreshRef.current = poller.refresh
    document.addEventListener('visibilitychange', becameVisible)
    return () => { poller.stop(); document.removeEventListener('visibilitychange', becameVisible); refreshRef.current = () => {} }
  }, [load, merge])
  return { data, error, loading, refresh }
}
