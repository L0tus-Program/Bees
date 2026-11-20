import { afterEach, describe, expect, it, vi } from 'vitest'
import { request, requestBinary, setCsrfToken } from './client'

const limit = 64 * 1024
const encode = (value: string) => new TextEncoder().encode(value)
const diagnostic = { http_status: 400, parameter: 'tools', provider_message: 'Unsupported tools.' }
const payload = { error: { code: 'provider_rejected', diagnostic, message: 'PRIVATE' } }
function padded(size: number) {
  const text = JSON.stringify(payload)
  return encode(text + ' '.repeat(size - encode(text).length))
}
function transport(chunks: Uint8Array[], status = 503, headers: Record<string, string> = {}, cleanupFails = false) {
  const pull = vi.fn((controller: ReadableStreamDefaultController<Uint8Array>) => {
    const chunk = chunks.shift()
    if (chunk) controller.enqueue(chunk)
    else controller.close()
  })
  const cancel = vi.fn(() => cleanupFails ? Promise.reject(new Error('PRIVATE cleanup')) : undefined)
  const body = new ReadableStream<Uint8Array>({ pull, cancel }, { highWaterMark: 0 })
  const response = new Response(body, { status, headers })
  const json = vi.spyOn(response, 'json')
  let signal!: AbortSignal
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    signal = options.signal as AbortSignal
    signal.addEventListener('abort', () => { void body.cancel().catch(() => {}) }, { once: true })
    return response
  })
  vi.stubGlobal('fetch', fetcher)
  return { pull, cancel, body, json, fetcher, signal: () => signal }
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); setCsrfToken() })

describe('shared client bounded HTTP errors', () => {
  it.each([false, true])('accepts the exact limit and preserves sanitized diagnostics, fragmented=%s', async (fragmented) => {
    const bytes = padded(limit)
    const server = transport(fragmented ? [bytes.slice(0, 31), bytes.slice(31)] : [bytes])
    const result = await request('/sample').catch((error: unknown) => error)
    expect(result).toMatchObject({ code: 'provider_rejected', status: 503, diagnostic })
    expect(JSON.stringify(result)).not.toContain('PRIVATE')
    expect(server.json).not.toHaveBeenCalled()
    expect(server.signal().aborted).toBe(true)
    expect(server.body.locked).toBe(false)
  })
  it.each([undefined, '1', String(limit)])('refuses excess real bytes with Content-Length %s without trusting a valid prefix', async (length) => {
    const server = transport([padded(limit), encode(' '), encode('unread')], 409, length ? { 'Content-Length': length } : {})
    await expect(request('/sample', { body: {} })).rejects.toMatchObject({ code: 'invalid_response', status: 409, diagnostic: null })
    expect(server.pull).toHaveBeenCalledTimes(2)
    expect(server.cancel).toHaveBeenCalledTimes(1)
    expect(server.fetcher).toHaveBeenCalledTimes(1)
    expect(server.json).not.toHaveBeenCalled()
    expect(server.body.locked).toBe(false)
  })
  it('refuses an excessive announcement before reading and tolerates cleanup failure', async () => {
    const server = transport([encode('PRIVATE')], 502, { 'Content-Length': String(limit + 1) }, true)
    await expect(request('/sample')).rejects.toMatchObject({ code: 'invalid_response', diagnostic: null })
    expect(server.pull).not.toHaveBeenCalled()
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it.each([encode('not JSON'), encode('{"error":'), new Uint8Array([0xff]), new Uint8Array()])('sanitizes malformed error bytes %j', async (bytes) => {
    const server = transport([bytes])
    await expect(request('/sample')).rejects.toMatchObject({ code: 'invalid_response', diagnostic: null })
    expect(server.signal().aborted).toBe(true)
  })
  it.each([32, 512 * 1024])('uses the smaller of the error ceiling and explicit limit %i', async (explicit) => {
    const server = transport([encode('PRIVATE')], 500, { 'Content-Length': String(Math.min(limit, explicit) + 1) })
    await expect(request('/sample', { maxResponseBytes: explicit })).rejects.toMatchObject({ code: 'http_500' })
    expect(server.pull).not.toHaveBeenCalled()
    expect(server.signal().aborted).toBe(true)
  })
  it('keeps unlimited successful responses compatible', async () => {
    const value = { text: 'x'.repeat(limit + 1) }
    const server = transport([encode(JSON.stringify(value))], 200)
    await expect(request('/sample')).resolves.toEqual(value)
    expect(server.json).toHaveBeenCalledTimes(1)
    expect(server.signal().aborted).toBe(false)
  })
  it.each(['binary', 'restore', 'patch'] as const)('bounds errors from %s mutations without retry', async (kind) => {
    setCsrfToken('test-csrf')
    const server = transport([padded(limit + 1), encode('unread')])
    const outcome = kind === 'binary' ? requestBinary('/upload', encode('abc'), 'e30')
      : request('/sample', kind === 'restore' ? { restoreBody: new Blob(['test']), authenticated: true } : { method: 'PATCH', body: {}, authenticated: true })
    await expect(outcome).rejects.toMatchObject({ code: 'invalid_response' })
    expect(server.fetcher).toHaveBeenCalledTimes(1)
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it.each(['read', 'mutation', 'human'] as const)('closes an unfinished error on %s interruption and expires auth before reading', async (kind) => {
    vi.useFakeTimers()
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent })
    setCsrfToken('test-csrf')
    const human = new AbortController()
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
    let signal!: AbortSignal
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')), { once: true })
      return new Response(body, { status: 401 })
    })
    vi.stubGlobal('fetch', fetcher)
    const outcome = request('/sample', { authenticated: true, ...(kind === 'mutation' ? { body: {} } : {}), signal: human.signal, timeoutMs: 25 }).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(0)
    expect(dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'bees:session-expired' }))
    await expect(request('/private', { authenticated: true, body: {} })).rejects.toMatchObject({ code: 'csrf_missing' })
    if (kind === 'human') human.abort()
    else await vi.advanceTimersByTimeAsync(25)
    expect(await outcome).toMatchObject(kind === 'human' ? { name: 'AbortError' } : { code: kind === 'mutation' ? 'mutation_timeout' : 'request_timeout' })
    expect(signal.aborted).toBe(true)
    expect(body.locked).toBe(false)
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
