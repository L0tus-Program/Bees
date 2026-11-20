import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { request } from './client'

afterEach(() => vi.unstubAllGlobals())
describe('shared error transport over real loopback HTTP', () => {
  it.each(['announced', 'chunked', 'mutation', 'timeout', 'human'] as const)('disconnects an unfinished %s error without retry', async (scenario) => {
    const nativeFetch = fetch
    const human = new AbortController()
    let count = 0
    let closed!: () => void
    const disconnected = new Promise<void>((resolve) => { closed = resolve })
    const server = createServer((_incoming, response) => {
      count += 1
      response.on('close', closed)
      response.writeHead(503, { 'Content-Type': 'application/json', ...(scenario === 'announced' ? { 'Content-Length': '1000000000' } : {}) })
      response.flushHeaders()
      if (scenario === 'chunked' || scenario === 'mutation') response.write(' '.repeat(64 * 1024 + 1))
      if (scenario === 'human') human.abort()
      // No EOF: the client must close the transport, not wait for the server.
    })
    try {
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject); server.listen(0, '127.0.0.1', resolve)
      })
      const { port } = server.address() as AddressInfo
      vi.stubGlobal('fetch', (url: string, options: RequestInit) => nativeFetch(`http://127.0.0.1:${port}${url}`, options))
      const outcome = request('/sample', { signal: human.signal, timeoutMs: scenario === 'timeout' ? 500 : 5000, ...(scenario === 'mutation' ? { body: {} } : {}) })
      await expect(outcome).rejects.toMatchObject(scenario === 'human' ? { name: 'AbortError' } : { code: scenario === 'timeout' ? 'request_timeout' : 'invalid_response' })
      await disconnected
      expect(count).toBe(1)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  }, 10000)
})
