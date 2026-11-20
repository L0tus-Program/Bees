import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { previewArtifact } from './api'
import { agentId, artifact, conversationId } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
describe('preview transport over real loopback HTTP', () => {
  it.each(['announced', 'error', 'chunked', 'human'] as const)('disconnects without waiting for an unfinished %s body', async (scenario) => {
    const nativeFetch = fetch
    const human = new AbortController()
    let requests = 0
    let closed!: () => void
    const disconnected = new Promise<void>((resolve) => { closed = resolve })
    const server = createServer((_request, response) => {
      requests += 1
      response.on('close', closed)
      if (scenario === 'announced' || scenario === 'error') {
        response.writeHead(scenario === 'error' ? 503 : 200, { 'Content-Type': 'application/json', 'Content-Length': '1000000000' })
        response.flushHeaders()
      } else {
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.write(scenario === 'human' ? '{"text":' : ' '.repeat(512 * 1024 + 1))
        if (scenario === 'human') human.abort()
      }
      // Intentionally no EOF; only a client abort can finish the operation.
    })
    try {
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject); server.listen(0, '127.0.0.1', resolve)
      })
      const { port } = server.address() as AddressInfo
      vi.stubGlobal('fetch', (url: string, options: RequestInit) => nativeFetch(`http://127.0.0.1:${port}${url}`, options))
      const outcome = previewArtifact(agentId, conversationId, artifact, human.signal)
      await expect(outcome).rejects.toMatchObject(scenario === 'human' ? { name: 'AbortError' } : { code: scenario === 'error' ? 'http_503' : 'invalid_response' })
      await disconnected
      expect(requests).toBe(1)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  }, 10000)
})
