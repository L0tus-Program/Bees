import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { downloadArtifact } from './api'
import { agentId, artifact, binary, taskId } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

describe('artifact download transport over real loopback HTTP', () => {
  it.each(['headers', 'announced-error', 'chunked-error'] as const)('disconnects without waiting for an unfinished %s body', async (scenario) => {
    const nativeFetch = fetch
    let requests = 0
    let closed!: () => void
    const disconnected = new Promise<void>((resolve) => { closed = resolve })
    const server = createServer((_request, response) => {
      requests += 1
      response.on('close', closed)
      if (scenario === 'headers') {
        const headers = binary().headers
        headers.set('Content-Disposition', 'inline'); headers.set('Content-Length', '1000000000')
        response.writeHead(200, Object.fromEntries(headers))
        response.flushHeaders()
      } else if (scenario === 'announced-error') {
        response.writeHead(503, { 'Content-Type': 'application/json', 'Content-Length': '1000000000' })
        response.flushHeaders()
      } else {
        response.writeHead(409, { 'Content-Type': 'application/json' })
        response.write(JSON.stringify({ error: { code: 'artifact_not_ready' } }) + ' '.repeat(65537))
      }
      // Deliberately leave the body unfinished: completion requires client abort.
    })
    try {
      await new Promise<void>((resolve, reject) => {
        server.once('error', reject)
        server.listen(0, '127.0.0.1', resolve)
      })
      const { port } = server.address() as AddressInfo
      vi.stubGlobal('fetch', (url: string, options: RequestInit) => nativeFetch(`http://127.0.0.1:${port}${url}`, options))
      await expect(downloadArtifact(agentId, taskId, artifact)).rejects.toMatchObject({ code: scenario === 'headers' ? 'invalid_response' : scenario === 'announced-error' ? 'http_503' : 'http_409' })
      await disconnected
      expect(requests).toBe(1)
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  }, 10000)
})
