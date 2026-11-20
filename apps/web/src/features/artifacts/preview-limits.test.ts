import { afterEach, describe, expect, it, vi } from 'vitest'
import { request, setCsrfToken } from '../../api/client'
import { previewArtifact } from './api'
import { agentId, artifact, conversationId } from './fixtures'

const limit = 512 * 1024
const encode = (text: string) => new TextEncoder().encode(text)
const preview = { artifact, text: 'abc', truncated: false, total_bytes: 3 }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); setCsrfToken() })
function transport(chunks: Uint8Array[], status = 200, headers: Record<string, string> = {}, cancelFails = false) {
  let signal: AbortSignal | undefined
  const pull = vi.fn((controller: ReadableStreamDefaultController<Uint8Array>) => {
    const next = chunks.shift()
    if (next) controller.enqueue(next)
    else controller.close()
  })
  const cancel = vi.fn(() => cancelFails ? Promise.reject(new Error('private cleanup failure')) : undefined)
  const body = new ReadableStream<Uint8Array>({ pull, cancel }, { highWaterMark: 0 })
  const response = new Response(body, { status, headers })
  const json = vi.spyOn(response, 'json')
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    signal = options.signal as AbortSignal
    signal.addEventListener('abort', () => { void body.cancel().catch(() => {}) }, { once: true })
    return response
  })
  vi.stubGlobal('fetch', fetcher)
  return { pull, cancel, json, fetcher, body, signal: () => signal }
}
function padded(size: number, value: unknown = preview) {
  const text = JSON.stringify(value)
  return encode(text + ' '.repeat(size - encode(text).byteLength))
}
const read = (signal?: AbortSignal) => previewArtifact(agentId, conversationId, artifact, signal)

describe('bounded preview envelope shared by preview and revision editor', () => {
  it.each([false, true])('accepts the exact byte ceiling, fragmented=%s', async (fragmented) => {
    const bytes = padded(limit)
    const split = bytes.indexOf(0xc3) + 1
    const server = transport(fragmented ? [bytes.slice(0, split), bytes.slice(split)] : [bytes])
    expect((await read()).text).toBe('abc')
    expect(server.json).not.toHaveBeenCalled()
    expect(server.signal()?.aborted).toBe(true)
    expect(server.body.locked).toBe(false)
  })
  it('accepts 64 KiB of escaped text plus metadata without truncating an editable file', async () => {
    const text = '\x01'.repeat(65536)
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', encode(text)))
    const expected = { ...artifact, size_bytes: 65536, sha256: Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('') }
    const bytes = encode(JSON.stringify({ artifact: expected, text, truncated: false, total_bytes: 65536 }))
    expect(bytes.length).toBeGreaterThan(6 * 65536)
    transport([bytes])
    expect(await previewArtifact(agentId, conversationId, expected)).toMatchObject({ text, truncated: false })
  })
  it.each([undefined, '1', String(limit)])('rejects excess bytes despite Content-Length %s', async (length) => {
    const server = transport([padded(limit), encode(' '), encode('unread')], 200, length ? { 'Content-Length': length } : {})
    await expect(read()).rejects.toMatchObject({ code: 'invalid_response' })
    expect(server.pull).toHaveBeenCalledTimes(2)
    expect(server.cancel).toHaveBeenCalledTimes(1)
    expect(server.json).not.toHaveBeenCalled()
    expect(server.body.locked).toBe(false)
    expect(server.fetcher).toHaveBeenCalledTimes(1)
  })
  it('rejects one oversized chunk and preserves the failure if cleanup fails', async () => {
    const server = transport([padded(limit + 1), encode('unread')], 200, {}, true)
    await expect(read()).rejects.toMatchObject({ code: 'invalid_response' })
    expect(server.pull).toHaveBeenCalledTimes(1)
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it.each([200, 503])('rejects oversized announced bytes without consuming status %i', async (status) => {
    const server = transport([encode('private')], status, { 'Content-Length': String(limit + 1) })
    await expect(read()).rejects.toMatchObject({ code: status === 200 ? 'invalid_response' : 'http_503', status })
    expect(server.pull).not.toHaveBeenCalled()
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it('never trusts a valid error prefix of an oversized body', async () => {
    const server = transport([padded(limit, { error: { code: 'artifact_not_ready' } }), encode(' '), encode('unread')], 409)
    await expect(read()).rejects.toMatchObject({ code: 'http_409', status: 409, diagnostic: null })
    expect(server.pull).toHaveBeenCalledTimes(2)
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it.each([encode('not JSON'), new Uint8Array([0xff]), encode('{"text":')])('refuses malformed success bytes %j', async (bytes) => {
    const server = transport([bytes])
    await expect(read()).rejects.toMatchObject({ code: 'invalid_response' })
    expect(server.signal()?.aborted).toBe(true)
  })
  it('preserves small sanitized errors', async () => {
    transport([encode('{"error":{"code":"artifact_preview_unavailable","message":"private"}}')], 422)
    await expect(read()).rejects.toMatchObject({ code: 'artifact_preview_unavailable', status: 422 })
  })
  it.each(['timeout', 'human'] as const)('closes an unfinished body on %s and expires the session before reading', async (reason) => {
    vi.useFakeTimers()
    const human = new AbortController()
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent }); setCsrfToken('test-csrf')
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    let signal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
      signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => streamController.error(new DOMException('aborted', 'AbortError')), { once: true })
      return new Response(body, { status: 401 })
    }))
    const outcome = read(human.signal).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(0)
    expect(dispatchEvent).toHaveBeenCalledTimes(1)
    expect(dispatchEvent.mock.calls[0][0].type).toBe('bees:session-expired')
    await expect(request('/private', { authenticated: true, body: {} })).rejects.toMatchObject({ code: 'csrf_missing' })
    if (reason === 'timeout') await vi.advanceTimersByTimeAsync(10000)
    else human.abort()
    expect(await outcome).toMatchObject(reason === 'timeout' ? { code: 'request_timeout' } : { name: 'AbortError' })
    expect(signal?.aborted).toBe(true)
    expect(body.locked).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('expires sessions even when announced error bytes are refused unread', async () => {
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent })
    const server = transport([encode('private')], 401, { 'Content-Length': String(limit + 1) })
    await expect(read()).rejects.toMatchObject({ code: 'http_401' })
    expect(dispatchEvent).toHaveBeenCalledTimes(1)
    expect(server.pull).not.toHaveBeenCalled()
  })
})
