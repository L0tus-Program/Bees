import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadArtifact } from './api'
import { agentId, artifact, binary, taskId } from './fixtures'

const limit = 64 * 1024
const encode = (text: string) => new TextEncoder().encode(text)
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function transport(chunks: Uint8Array[], status = 409, headers: Record<string, string> = {}, cancelFails = false) {
  let signal: AbortSignal | undefined
  const pull = vi.fn((controller: ReadableStreamDefaultController<Uint8Array>) => {
    const next = chunks.shift()
    if (next) controller.enqueue(next)
    else controller.close()
  })
  const cancel = vi.fn(() => { if (cancelFails) return Promise.reject(new Error('private cleanup failure')) })
  const body = new ReadableStream<Uint8Array>({ pull, cancel }, { highWaterMark: 0 })
  const response = new Response(body, { status, headers })
  const json = vi.spyOn(response, 'json')
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    signal = options.signal as AbortSignal
    signal.addEventListener('abort', () => { void body.cancel().catch(() => {}) }, { once: true })
    return response
  })
  vi.stubGlobal('fetch', fetcher)
  return { response, pull, cancel, json, fetcher, signal: () => signal }
}

function envelope(size: number) {
  const text = JSON.stringify({ error: { code: 'artifact_not_ready' }, private: 'á' })
  const bytes = encode(text)
  return encode(text + ' '.repeat(size - bytes.byteLength))
}

describe('bounded artifact download errors and transport cleanup', () => {
  it.each([0, 1])('accepts an error envelope at the byte limit, fragmented=%i', async (fragmented) => {
    const bytes = envelope(limit)
    const split = bytes.indexOf(0xc3) + 1
    const server = transport(fragmented ? [bytes.slice(0, split), bytes.slice(split, 1024), bytes.slice(1024)] : [bytes])
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'artifact_not_ready', status: 409 })
    expect(server.json).not.toHaveBeenCalled()
    expect(server.signal()?.aborted).toBe(true)
    expect(server.response.body?.locked).toBe(false)
    expect(server.fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([undefined, '1', String(limit)])('refuses excess actual bytes with Content-Length %s', async (length) => {
    const server = transport([envelope(limit), encode(' '), encode('unread private tail')], 503, length ? { 'Content-Length': length } : {})
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'http_503', status: 503 })
    expect(server.pull).toHaveBeenCalledTimes(2)
    expect(server.cancel).toHaveBeenCalledTimes(1)
    expect(server.json).not.toHaveBeenCalled()
    expect(server.response.body?.locked).toBe(false)
  })
  it('refuses a single oversized chunk without trusting its valid error prefix', async () => {
    const server = transport([envelope(limit + 1), encode('unread')])
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'http_409', status: 409 })
    expect(server.pull).toHaveBeenCalledTimes(1)
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it('refuses an announced oversized error before reading any bytes', async () => {
    const server = transport([envelope(100)], 500, { 'Content-Length': '1000000000' })
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'http_500', status: 500 })
    expect(server.pull).not.toHaveBeenCalled()
    expect(server.cancel).toHaveBeenCalledTimes(1)
  })
  it.each([encode('private non-JSON response'), new Uint8Array([0xff]), encode('{"error":{"code":"<private>"}}'), encode('{"error":{"code":"artifact_not_ready"}')])('sanitizes malformed error bytes %j', async (bytes) => {
    const server = transport([bytes])
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'http_409', status: 409 })
    expect(server.signal()?.aborted).toBe(true)
  })
  it('announces expired sessions immediately even with an oversized error body', async () => {
    const dispatchEvent = vi.fn(); vi.stubGlobal('window', { dispatchEvent })
    const server = transport([envelope(limit + 1)], 401)
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'http_401', status: 401 })
    expect(dispatchEvent).toHaveBeenCalledTimes(1)
    expect(dispatchEvent.mock.calls[0][0].type).toBe('bees:session-expired')
    expect(server.signal()?.aborted).toBe(true)
  })
  it.each<Record<string, string>>([
    { 'Content-Disposition': 'inline' },
    { 'Content-Disposition': "attachment; filename*=UTF-8''%invalid" },
    { 'Content-Type': 'text/html' },
    { 'Content-Length': '1000000000' },
  ])('closes an unread success body with refused headers %j', async (overrides) => {
    const server = transport([encode('private body')], 200, Object.fromEntries(binary('abc', overrides).headers))
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toBeInstanceOf(Error)
    expect(server.pull).not.toHaveBeenCalled()
    expect(server.cancel).toHaveBeenCalledTimes(1)
    expect(server.signal()?.aborted).toBe(true)
  })
  it('preserves integrity failure if cancelling the excess body fails', async () => {
    const server = transport([encode('abcd'), encode('unread')], 200, Object.fromEntries(binary().headers), true)
    await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: 'artifact_integrity_failed' })
    expect(server.pull).toHaveBeenCalledTimes(1)
    expect(server.cancel).toHaveBeenCalledTimes(1)
    expect(server.response.body?.locked).toBe(false)
  })
  it('aborts transport after a verified successful download without damaging the blob', async () => {
    const server = transport([encode('a'), encode('bc')], 200, Object.fromEntries(binary().headers))
    const blob = await downloadArtifact(agentId, taskId, artifact)
    expect(await blob.text()).toBe('abc')
    expect(server.signal()?.aborted).toBe(true)
    expect(server.response.body?.locked).toBe(false)
  })
  it.each(['timeout', 'human'] as const)('closes an in-flight error body on %s cancellation', async (reason) => {
    vi.useFakeTimers()
    const human = new AbortController()
    let streamController: ReadableStreamDefaultController<Uint8Array>
    let signal: AbortSignal | undefined
    const body = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller } })
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
      signal = options.signal as AbortSignal
      signal.addEventListener('abort', () => streamController.error(new DOMException('aborted', 'AbortError')), { once: true })
      return new Response(body, { status: 503 })
    }))
    const outcome = downloadArtifact(agentId, taskId, artifact, human.signal).catch((error: unknown) => error)
    await vi.advanceTimersByTimeAsync(0)
    if (reason === 'timeout') await vi.advanceTimersByTimeAsync(30000)
    else human.abort()
    const failure = await outcome
    if (reason === 'timeout') expect(failure).toMatchObject({ code: 'request_timeout' })
    else expect(failure).toMatchObject({ name: 'AbortError' })
    expect(signal?.aborted).toBe(true)
    expect(body.locked).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
