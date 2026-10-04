import { useEffect, useReducer, useState } from 'react'
import { getHealth, HealthError } from '../../api/health'
import type { HealthErrorCode, HealthResponse } from '../../api/health'

type ServiceState =
  | { status: 'loading' }
  | { status: 'online'; response: HealthResponse; checkedAt: Date }
  | { status: 'offline'; error: HealthErrorCode }

export function useServiceHealth() {
  const [state, setState] = useState<ServiceState>({ status: 'loading' })
  const [attempt, retry] = useReducer((value: number) => value + 1, 0)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    getHealth({ signal: controller.signal }).then(
      (response) => {
        if (active) setState({ status: 'online', response, checkedAt: new Date() })
      },
      (error: unknown) => {
        if (active) setState({ status: 'offline', error: error instanceof HealthError ? error.code : 'network' })
      },
    )
    return () => {
      active = false
      controller.abort()
    }
  }, [attempt])

  return {
    state,
    retry: () => {
      setState({ status: 'loading' })
      retry()
    },
  }
}
