import { useCallback, useEffect, useReducer, useState } from 'react'
import { asApiError } from '../api/client'
import type { ApiError } from '../api/client'

type Resource<T> = { state: 'loading' } | { state: 'error'; error: ApiError } | { state: 'ready'; data: T }

export function useResource<T>(load: (signal: AbortSignal) => Promise<T>) {
  const [resource, setResource] = useState<Resource<T>>({ state: 'loading' })
  const [attempt, refresh] = useReducer((value: number) => value + 1, 0)
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    void load(controller.signal).then(
      (data) => { if (active) setResource({ state: 'ready', data }) },
      (error: unknown) => { if (active) setResource({ state: 'error', error: asApiError(error) }) },
    )
    return () => { active = false; controller.abort() }
  }, [load, attempt])
  // Recarregar mantém os dados atuais na tela (sem trocar por "carregando"), evitando saltos de layout.
  const reload = useCallback(() => { setResource((current) => current.state === 'ready' ? current : { state: 'loading' }); refresh() }, [])
  return { resource, reload }
}
