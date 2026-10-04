import { afterEach, describe, expect, it, vi } from 'vitest'
import { getHealth } from './health'

const healthy = { status: 'ok', service: 'bees-api', version: '0.1.0', stage: 'foundation' }

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('getHealth', () => {
  it('consulta o endpoint real sem cache e aceita o contrato da fundação', async () => {
    const request = vi.fn().mockResolvedValue(Response.json(healthy))
    vi.stubGlobal('fetch', request)
    await expect(getHealth()).resolves.toEqual(healthy)
    expect(request).toHaveBeenCalledWith('/api/v1/health', expect.objectContaining({
      cache: 'no-store', credentials: 'same-origin', signal: expect.any(AbortSignal),
    }))
  })

  it.each([
    { status: 'ok', service: 'another-api', version: '0.1.0', stage: 'foundation' },
    { ...healthy, status: 'unavailable' },
    { ...healthy, version: '' },
    { ...healthy, stage: 'ready' },
    null,
  ])('rejeita resposta incompatível sem afirmar conexão válida: %j', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))
    await expect(getHealth()).rejects.toMatchObject({ code: 'contract' })
  })

  it('diferencia falha HTTP de indisponibilidade de rede', async () => {
    const request = vi.fn().mockResolvedValue(new Response('', { status: 503 }))
    vi.stubGlobal('fetch', request)
    await expect(getHealth()).rejects.toMatchObject({ code: 'http' })
    request.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(getHealth()).rejects.toMatchObject({ code: 'network' })
  })

  it('rejeita HTML servido por engano no endpoint da API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>SPA</html>')))
    await expect(getHealth()).rejects.toMatchObject({ code: 'contract' })
  })

  it('encerra a requisição quando excede o prazo, sem tentativas automáticas', async () => {
    vi.useFakeTimers()
    const request = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    vi.stubGlobal('fetch', request)
    const result = expect(getHealth({ timeoutMs: 100 })).rejects.toMatchObject({ code: 'timeout' })
    await vi.advanceTimersByTimeAsync(100)
    await result
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('preserva cancelamento solicitado pela interface como cancelamento', async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const controller = new AbortController()
    const result = expect(getHealth({ signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort()
    await result
  })
})
