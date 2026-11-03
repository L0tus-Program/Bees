export const responseIssues = ['invalid_json', 'content_type', 'content_encoding', 'choices', 'message', 'tool_calls', 'tool_call', 'tool_arguments_json', 'tool_arguments_schema', 'tool_not_offered', 'duplicate_tool_call', 'finish_reason', 'content', 'usage', 'error_envelope'] as const
export interface ProviderDiagnostic { response_issue?: typeof responseIssues[number]; provider_message?: string | null; http_status: number | null; parameter: string | null }
export function providerDiagnostic(value: unknown): ProviderDiagnostic | null {
  if (!record(value)
    || !(value.response_issue === undefined || responseIssues.includes(value.response_issue as typeof responseIssues[number]))
    || !(value.provider_message === undefined || value.provider_message === null || typeof value.provider_message === 'string' && value.provider_message.length <= 1001)
    || !(value.http_status === null || Number.isInteger(value.http_status) && Number(value.http_status) >= 400 && Number(value.http_status) <= 599)
    || !(value.parameter === null || typeof value.parameter === 'string' && ['tools', 'tools.function.parameters', 'tools.function.strict', 'messages', 'model', 'store', 'stream'].includes(value.parameter))) return null
  return { ...(value.response_issue ? { response_issue: value.response_issue as typeof responseIssues[number] } : {}), provider_message: value.provider_message as string | null | undefined, http_status: value.http_status as number | null, parameter: value.parameter as string | null }
}

export class ApiError extends Error {
  constructor(public readonly code: string, public readonly status = 0, public readonly diagnostic: ProviderDiagnostic | null = null) {
    // Keep arbitrary response text out; diagnostics use the separate bounded contract.
    super(code)
    this.name = 'ApiError'
  }
}

let csrfToken: string | undefined
export function setCsrfToken(token?: string) { csrfToken = token }

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

type RequestOptions = {
  body?: unknown
  binaryBody?: Blob
  binaryMetadata?: string
  restoreBody?: Blob
  method?: 'PATCH' | 'PUT'
  signal?: AbortSignal
  timeoutMs?: number
  authenticated?: boolean
}

export async function request(path: string, options: RequestOptions = {}): Promise<unknown> {
  const mutation = options.body !== undefined || options.binaryBody !== undefined || options.restoreBody !== undefined
  if (mutation && options.authenticated && !csrfToken) throw new ApiError('csrf_missing')
  const controller = new AbortController()
  let timedOut = false
  const abort = () => controller.abort(options.signal?.reason)
  if (options.signal?.aborted) abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs ?? 10000)
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (mutation) headers['Content-Type'] = options.restoreBody ? 'application/zip' : options.binaryBody ? 'application/octet-stream' : 'application/json'
  if (options.restoreBody) headers['X-Bees-Restore-Consent'] = 'restore_inactive_installation'
  if (options.binaryMetadata) headers['X-Bees-Artifact'] = options.binaryMetadata
  if (mutation && options.authenticated && csrfToken) headers['X-Bees-CSRF'] = csrfToken

  try {
    const response = await fetch(`/api/v1${path}`, {
      method: mutation ? (options.method ?? 'POST') : 'GET', headers, credentials: 'same-origin', cache: 'no-store',
      ...(mutation ? { body: options.restoreBody ?? options.binaryBody ?? JSON.stringify(options.body) } : {}), signal: controller.signal, redirect: 'error',
    })
    let body: unknown
    if (response.status !== 204) {
      try { body = await response.json() } catch (error) {
        if (controller.signal.aborted) throw error
        throw new ApiError('invalid_response', response.status)
      }
    }
    if (!response.ok) {
      if (response.status === 401 && options.authenticated) {
        setCsrfToken()
        if (typeof window !== 'undefined') window.dispatchEvent(new Event('bees:session-expired'))
      }
      const code = record(body) && record(body.error) && typeof body.error.code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(body.error.code)
        ? body.error.code : `http_${response.status}`
      throw new ApiError(code, response.status, record(body) && record(body.error) ? providerDiagnostic(body.error.diagnostic) : null)
    }
    return body
  } catch (error) {
    if (options.signal?.aborted) throw error
    if (timedOut) throw new ApiError(mutation ? 'mutation_timeout' : 'request_timeout')
    if (error instanceof ApiError) throw error
    throw new ApiError(mutation ? 'mutation_network' : 'network_error')
  } finally {
    clearTimeout(timeout)
    options.signal?.removeEventListener('abort', abort)
  }
}

// Fetch supplies Content-Length from the complete Blob; scripts cannot set that header.
// Authentication and CSRF follow the same path as JSON mutations.
export function requestBinary(path: string, bytes: Uint8Array, metadata: string, signal?: AbortSignal) {
  if (bytes.byteLength > 25 * 1024 * 1024 || !/^[A-Za-z0-9_-]{1,2048}$/.test(metadata)) throw new ApiError('invalid_request')
  return request(path, { binaryBody: new Blob([bytes.slice().buffer]), binaryMetadata: metadata, method: 'PUT', authenticated: true, signal, timeoutMs: 60000 })
}

export function asApiError(error: unknown) {
  return error instanceof ApiError ? error : new ApiError('unknown_error')
}
