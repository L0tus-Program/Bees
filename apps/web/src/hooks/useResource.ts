import { useCallback, useEffect, useReducer, useState } from 'react'
import { asApiError } from '../api/client'
import type { ApiError } from '../api/client'

type Resource<T> = { state: 'loading' } | { state: 'error'; error: ApiError } | { state: 'ready'; data: T; refreshing?: boolean; refreshError?: ApiError }

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>, keepOnError?: (error: ApiError) => boolean) {
  const [resource, setResource] = useState<Resource<T>>({ state: 'loading' })
  const [attempt, refresh] = useReducer((value: number) => value + 1, 0)
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    void load(controller.signal).then(
      (data) => { if (active) setResource({ state: 'ready', data }) },
      (failure: unknown) => {
        if (!active) return
        const error = asApiError(failure)
        setResource((current) => current.state === 'ready' && keepOnError?.(error)
          ? { state: 'ready', data: current.data, refreshError: error }
          : { state: 'error', error })
      },
    )
    return () => { active = false; controller.abort() }
  }, [load, attempt, keepOnError])
  // Recarregar mantém os dados atuais na tela (sem trocar por "carregando"), evitando saltos de layout.
  const reload = useCallback(() => {
    setResource((current) => current.state === 'ready' ? { ...current, refreshing: true } : { state: 'loading' })
    refresh()
  }, [])
  return { resource, reload }
}
