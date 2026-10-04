export interface HealthResponse {
  status: 'ok'
  service: 'bees-api'
  version: string
  stage: 'foundation'
}

export type HealthErrorCode = 'network' | 'timeout' | 'http' | 'contract'

export class HealthError extends Error {
  constructor(public readonly code: HealthErrorCode) {
    super(code)
    this.name = 'HealthError'
  }
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) return false
  const response = value as Record<string, unknown>
  return response.status === 'ok' && response.service === 'bees-api'
    && response.stage === 'foundation' && typeof response.version === 'string'
    && response.version.length > 0
}

export async function getHealth(options: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<HealthResponse> {
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort(options.signal?.reason)
  if (options.signal?.aborted) abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs ?? 5000)

  try {
    const response = await fetch('/api/v1/health', {
      signal: controller.signal,
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
      cache: 'no-store',
    })
    if (!response.ok) throw new HealthError('http')
    let body: unknown
    try {
      body = await response.json()
    } catch (error) {
      if (controller.signal.aborted) throw error
      throw new HealthError('contract')
    }
    if (!isHealthResponse(body)) throw new HealthError('contract')
    return body
  } catch (error) {
    if (timedOut) throw new HealthError('timeout')
    if (options.signal?.aborted) throw error
    if (error instanceof HealthError) throw error
    throw new HealthError('network')
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abort)
  }
}
