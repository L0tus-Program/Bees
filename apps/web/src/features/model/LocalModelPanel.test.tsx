// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { LocalModelPanel } from './LocalModelPanel'
import { localModelResources } from '../../locales/localModel'
import { productResources } from '../../locales/product'
import { setCsrfToken } from '../../api/client'
import { registerConfirmHost } from '../../components/confirmAction'
import type { ConfirmRequest } from '../../components/confirmAction'

const status = {
  endpoint: 'http://127.0.0.1:11434', reachable: true, version: '0.40.6',
  models: [{ name: 'qwen3:1.7b', size_bytes: 1400000000, modified_at: null }],
  suggested: [
    { id: 'qwen3:1.7b', name: 'Qwen 3 1.7B', download_bytes: 1400000000, min_memory_gb: 4, tool_calls: true, source: 'https://ollama.com/library/qwen3', note: 'small' },
    { id: 'qwen3:8b', name: 'Qwen 3 8B', download_bytes: 5200000000, min_memory_gb: 12, tool_calls: true, source: 'https://ollama.com/library/qwen3', note: 'quality' },
  ],
  pulls: [],
}
const pull = { id: 'p1', model: 'qwen3:8b', endpoint: status.endpoint, status: 'running', phase: 'downloading', total_bytes: 100, completed_bytes: 40, error: null, started_at: 'x', finished_at: null }
let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
beforeEach(async () => {
  sessionStorage.clear()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { localModel: localModelResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); registerConfirmHost(null); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render(onSelect = vi.fn(), endpoint = status.endpoint) {
  await act(async () => root.render(<I18nextProvider i18n={i18n}><LocalModelPanel endpoint={endpoint} model="" onSelect={onSelect} /></I18nextProvider>))
  return onSelect
}
function button(text: string) { return [...container.querySelectorAll('button')].find((item) => item.textContent === text) }

it.each(['remount', 'poll', 'cancel', 'lookup'] as const)('retains accepted running intent until resolution via %s', async (mode) => {
  vi.useFakeTimers()
  try {
    let requestId = '', lost = false, terminal = false
    const writes: string[] = []
    const key = `bees.local-pull-pending.v1.${status.endpoint}`
    const completed = { ...pull, status: 'completed', phase: 'success', finished_at: '2026-11-03T00:01:00Z' }
    vi.stubGlobal('confirm', vi.fn(() => true))
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        writes.push(url)
        if (url.endsWith('/cancel')) return Response.json({ ...pull, status: 'cancelled', finished_at: completed.finished_at })
        requestId = JSON.parse(options.body as string).client_request_id
        return Response.json(pull)
      }
      if (url.includes('/pull-requests/') || url.includes('/pulls/p1')) {
        if (lost) return Response.json({ error: { code: 'pull_not_found', message: 'x' } }, { status: 404 })
        const snapshot = terminal ? completed : pull
        return Response.json(url.includes('/pull-requests/') ? { client_request_id: requestId, pull: snapshot } : snapshot)
      }
      return Response.json(status)
    }))
    await render()
    await act(async () => button('Baixar')!.click())
    expect(sessionStorage.getItem(key)).toContain(requestId)
    expect(container.querySelector('progress')).not.toBeNull()
    expect(button('Consultar pedido de download')).toBeUndefined()
    if (mode === 'remount' || mode === 'poll') {
      lost = true
      if (mode === 'remount') { await act(async () => root.render(null)); await render() }
      else await act(async () => vi.advanceTimersByTimeAsync(1000))
      expect(button('Consultar pedido de download')).toBeTruthy()
      await act(async () => button('Consultar pedido de download')!.click())
      await act(async () => button('Verificar de novo')!.click())
      expect(button('Baixar')?.disabled).toBe(true)
      expect(button('Remover')?.disabled).toBe(true)
      expect(sessionStorage.getItem(key)).toContain(requestId)
    } else {
      if (mode === 'cancel') await act(async () => button('Cancelar download')!.click())
      else {
        await act(async () => root.render(null)); await render()
        await act(async () => button('Consultar pedido de download')!.click())
        expect(sessionStorage.getItem(key)).toContain(requestId)
        terminal = true
        await act(async () => vi.advanceTimersByTimeAsync(1000))
      }
      expect(sessionStorage.getItem(key)).toBeNull()
      expect(button('Baixar')?.disabled).toBe(false)
    }
    expect(writes).toEqual(mode === 'cancel' ? ['/api/v1/local-models/pulls', '/api/v1/local-models/pulls/p1/cancel'] : ['/api/v1/local-models/pulls'])
  } finally { vi.useRealTimers() }
})

it('lists installed models and selects one', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(status)))
  const onSelect = await render()
  await vi.waitFor(() => expect(container.textContent).toContain('Ollama conectado (versão 0.40.6)'))
  await act(async () => button('Usar este modelo')!.click())
  expect(onSelect).toHaveBeenCalledWith('qwen3:1.7b')
})

it.each(['poll', 'cancel', 'lookup'] as const)('settles terminal receipts via %s and preserves intent on cleanup failure', async (source) => {
  vi.useFakeTimers()
  try {
    let requestId = '', terminal = false
    const key = `bees.local-pull-pending.v1.${status.endpoint}`
    const storage = sessionStorage
    vi.stubGlobal('confirm', vi.fn(() => true))
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      const snapshot = terminal ? { ...pull, status: 'failed', error: 'pull_failed', finished_at: '2026-11-03T00:01:00Z' } : pull
      if (options.method === 'POST' && !url.endsWith('/cancel')) {
        requestId = JSON.parse(options.body as string).client_request_id
        return Response.json(pull)
      }
      if (url.includes('/pull-requests/')) return Response.json({ client_request_id: requestId, pull: snapshot })
      if (url.includes('/pulls/p1')) return Response.json(snapshot)
      return Response.json(status)
    }))
    await render()
    await act(async () => button('Baixar')!.click())
    if (source === 'lookup') { await act(async () => root.render(null)); await render() }
    vi.stubGlobal('sessionStorage', { getItem: storage.getItem.bind(storage), setItem: storage.setItem.bind(storage), removeItem: () => { throw new Error('private cleanup failure') } })
    terminal = true
    if (source === 'poll') await act(async () => vi.advanceTimersByTimeAsync(1000))
    else await act(async () => button(source === 'cancel' ? 'Cancelar download' : 'Consultar pedido de download')!.click())
    expect(container.querySelector('.local-model-pull')?.textContent).toContain('falhou')
    expect(sessionStorage.getItem(key)).toContain(requestId)
    expect(button('Baixar')?.disabled).toBe(true)
    expect(button('Remover')?.disabled).toBe(true)
    expect(container.textContent).not.toContain('private cleanup failure')
    expect(button('Liberar novo pedido')).toBeTruthy()
    vi.stubGlobal('sessionStorage', storage)
    await act(async () => button('Liberar novo pedido')!.click())
    expect(button('Baixar')?.disabled).toBe(false)
    expect(sessionStorage.getItem(key)).toBeNull()
  } finally { vi.useRealTimers() }
})

it('never clears a preserved request when a different catalog pull finishes', async () => {
  vi.useFakeTimers()
  try {
    const key = `bees.local-pull-pending.v1.${status.endpoint}`
    const intent = { endpoint: status.endpoint, model: 'other:latest', clientRequestId: crypto.randomUUID() }
    sessionStorage.setItem(key, JSON.stringify(intent))
    vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json(url.includes('/pulls/p1')
      ? { ...pull, status: 'completed', phase: 'success', finished_at: '2026-11-03T00:01:00Z' }
      : { ...status, pulls: [pull] })))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(sessionStorage.getItem(key)).toBe(JSON.stringify(intent))
    expect(button('Baixar')?.disabled).toBe(true)
    expect(button('Consultar pedido de download')).toBeTruthy()
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  } finally { vi.useRealTimers() }
})

it('asks for confirmation with size and source before downloading, then follows progress', async () => {
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(JSON.parse(options.body as string)); return Response.json(pull, { status: 202 }) }
    if (url.includes('/pulls/p1')) return Response.json({ ...pull, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success', completed_bytes: 100 })
    return Response.json(status)
  }))
  const confirm = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  await render()
  await vi.waitFor(() => expect(button('Baixar')).toBeTruthy())
  await act(async () => button('Baixar')!.click())
  expect(confirm.mock.calls[0][0]).toContain('5,2 GB')
  expect(confirm.mock.calls[0][0]).toContain('https://ollama.com/library/qwen3')
  expect(writes).toHaveLength(0)
  await act(async () => button('Baixar')!.click())
  expect(writes[0]).toMatchObject({ model: 'qwen3:8b', confirm_download: true, endpoint: 'http://127.0.0.1:11434' })
  expect(button('Cancelar download')).toBeTruthy()
  expect(container.querySelector('progress')?.getAttribute('value')).toBe('40')
  await vi.waitFor(() => expect(container.textContent).toContain('qwen3:8b está pronto'), { timeout: 3000 })
})

it('explains how to start Ollama when unreachable and offers no download', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...status, reachable: false, version: null, models: [] })))
  await render()
  await vi.waitFor(() => expect(container.textContent).toContain('--profile local-model'))
  expect(button('Baixar')).toBeUndefined()
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(localModelResources[language]).sort()).toEqual(Object.keys(localModelResources['pt-BR']).sort())
})

it('recovers when Bees restarts during a download instead of staying stuck', async () => {
  let installed = false
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/pulls/p1')) { installed = true; return Response.json({ error: { code: 'pull_not_found', message: 'x' } }, { status: 404 }) }
    return Response.json(installed ? { ...status, models: [...status.models, { name: 'qwen3:8b', size_bytes: 5200000000, modified_at: null }] } : { ...status, pulls: [pull] })
  }))
  await render()
  await vi.waitFor(() => expect(button('Cancelar download')).toBeTruthy())
  await vi.waitFor(() => expect(container.textContent).toContain('acompanhamento do download de qwen3:8b foi perdido'), { timeout: 3000 })
  expect(button('Cancelar download')).toBeFalsy()
  expect(container.querySelector('progress')).toBeNull()
  await vi.waitFor(() => expect(container.textContent).toContain('qwen3:8b'))
})

it('keeps following the download after a transient read failure', async () => {
  let reads = 0
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.includes('/pulls/p1')) { reads += 1; return reads === 1 ? Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 }) : Response.json({ ...pull, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success', completed_bytes: 100 }) }
    return Response.json({ ...status, pulls: [pull] })
  }))
  await render()
  await vi.waitFor(() => expect(container.textContent).toContain('qwen3:8b está pronto'), { timeout: 5000 })
  expect(reads).toBe(2)
})

const otherEndpoint = 'http://127.0.0.1:11435'
const otherStatus = { ...status, endpoint: otherEndpoint, models: [{ name: 'modelo-outro:latest', size_bytes: 12, modified_at: null }] }
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

it('ignores an old status response after changing the Ollama endpoint', async () => {
  const old = deferred<Response>()
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('11435') ? Promise.resolve(Response.json(otherStatus)) : old.promise))
  await render()
  await render(vi.fn(), otherEndpoint)
  expect(container.textContent).toContain('modelo-outro:latest')
  await act(async () => old.resolve(Response.json(status)))
  expect(container.textContent).toContain('modelo-outro:latest')
  expect(container.textContent).not.toContain('selecionado')
  expect(button('Usar este modelo')).toBeTruthy()
  const onSelect = await render(vi.fn(), otherEndpoint)
  await act(async () => button('Usar este modelo')!.click())
  expect(onSelect).toHaveBeenCalledWith('modelo-outro:latest')
})

it('drops old progress and ignores an in-flight poll when the endpoint changes', async () => {
  const poll = deferred<Response>()
  const fetcher = vi.fn((url: string) => {
    if (url.includes('/pulls/p1')) return poll.promise
    return Promise.resolve(Response.json(url.includes('11435') ? otherStatus : { ...status, pulls: [pull] }))
  })
  vi.stubGlobal('fetch', fetcher)
  await render()
  await vi.waitFor(() => expect(fetcher.mock.calls.some(([url]) => url.includes('/pulls/p1'))).toBe(true), { timeout: 2500 })
  await render(vi.fn(), otherEndpoint)
  expect(button('Cancelar download')).toBeUndefined()
  expect(container.querySelector('progress')).toBeNull()
  await act(async () => poll.resolve(Response.json({ ...pull, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success' })))
  expect(container.textContent).not.toContain('qwen3:8b está pronto')
  expect(container.textContent).toContain('modelo-outro:latest')
})

it.each(['Baixar', 'Remover'])('does not execute an old %s confirmation after changing endpoint', async (action) => {
  const confirmations: ConfirmRequest[] = []
  registerConfirmHost((request) => confirmations.push(request))
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(options.body); return Response.json(pull) }
    return Response.json(url.includes('11435') ? otherStatus : status)
  }))
  await render()
  await act(async () => button(action)!.click())
  expect(confirmations).toHaveLength(1)
  await render(vi.fn(), otherEndpoint)
  await act(async () => confirmations[0].resolve(true))
  expect(writes).toHaveLength(0)
  expect(container.textContent).toContain('modelo-outro:latest')
})

it('allows only one pending confirmation and download even with rapid clicks', async () => {
  const confirmations: ConfirmRequest[] = []
  registerConfirmHost((request) => confirmations.push(request))
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(options.body); return Response.json(pull) }
    return Response.json(status)
  }))
  await render()
  await act(async () => { button('Baixar')!.click(); button('Baixar')!.click() })
  expect(confirmations).toHaveLength(1)
  await act(async () => confirmations[0].resolve(true))
  expect(writes).toHaveLength(1)
})

it('ignores an old download receipt after changing endpoint', async () => {
  const receipt = deferred<Response>()
  const fetcher = vi.fn((url: string, options: RequestInit) => options.method === 'POST'
    ? receipt.promise : Promise.resolve(Response.json(url.includes('11435') ? otherStatus : status)))
  vi.stubGlobal('fetch', fetcher); vi.stubGlobal('confirm', () => true)
  await render()
  await act(async () => button('Baixar')!.click())
  await render(vi.fn(), otherEndpoint)
  await act(async () => receipt.resolve(Response.json(pull)))
  expect(container.querySelector('progress')).toBeNull()
  expect(button('Cancelar download')).toBeUndefined()
  expect(container.textContent).toContain('modelo-outro:latest')
})

it('cancels the current download explicitly and stops displaying progress', async () => {
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); return Response.json({ ...pull, status: 'cancelled', finished_at: '2026-11-03T00:01:00Z' }) }
    return Response.json({ ...status, pulls: [pull] })
  }))
  await render()
  await act(async () => { button('Cancelar download')!.click(); button('Cancelar download')!.click() })
  expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
  expect(container.textContent).toContain('Download cancelado')
  expect(container.querySelector('progress')).toBeNull()
})

it('rediscovers a server-side download when returning to its endpoint without cancelling it', async () => {
  const receipt = deferred<Response>()
  let started = false
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (options.method === 'POST') { started = true; writes.push(url); return receipt.promise }
    return Promise.resolve(Response.json(url.includes('11435') ? otherStatus : { ...status, pulls: started ? [pull] : [] }))
  })); vi.stubGlobal('confirm', () => true)
  await render()
  await act(async () => button('Baixar')!.click())
  await render(vi.fn(), otherEndpoint)
  await render()
  expect(container.querySelector('progress')?.value).toBe(40)
  expect(button('Cancelar download')).toBeTruthy()
  await act(async () => receipt.resolve(Response.json(pull)))
  expect(writes).toEqual(['/api/v1/local-models/pulls'])
})

it.each(['completed', 'failed'] as const)('does not announce cancellation if the download already %s', async (outcome) => {
  let reads = 0
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      writes.push(url)
      return Response.json({ ...pull, status: outcome, finished_at: '2026-11-03T00:01:00Z', phase: outcome === 'completed' ? 'success' : 'downloading', error: outcome === 'failed' ? 'pull_failed' : null })
    }
    reads += 1
    return Response.json({ ...status, pulls: [pull] })
  }))
  await render()
  await act(async () => button('Cancelar download')!.click())
  expect(container.textContent).not.toContain('Download cancelado')
  expect(container.querySelector('progress')).toBeNull()
  expect(container.textContent).toContain(outcome === 'completed' ? 'qwen3:8b está pronto' : 'O download de qwen3:8b falhou')
  expect(reads).toBe(outcome === 'completed' ? 2 : 1)
  expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
})

const unreachableWithPull = { ...status, reachable: false, version: null, models: [], pulls: [pull] }

it('shows and cancels a tracked download even when Ollama is unreachable', async () => {
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); return Response.json({ ...pull, status: 'cancelled', finished_at: '2026-11-03T00:01:00Z' }) }
    return Response.json(unreachableWithPull)
  }))
  await render()
  expect(container.textContent).toContain('O Ollama não respondeu')
  expect(container.querySelector('progress')?.value).toBe(40)
  expect(button('Baixar')).toBeUndefined()
  expect(button('Remover')).toBeUndefined()
  await act(async () => { button('Cancelar download')!.click(); button('Cancelar download')!.click() })
  expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
  expect(container.textContent).toContain('Download cancelado')
  expect(container.querySelector('progress')).toBeNull()
})

it('keeps progress while refreshing and enables cancellation after a valid unavailable snapshot', async () => {
  const refreshed = deferred<Response>()
  let reads = 0
  vi.stubGlobal('fetch', vi.fn(async () => {
    reads += 1
    return reads === 1 ? Response.json({ ...status, pulls: [pull] }) : refreshed.promise
  }))
  await render()
  await act(async () => button('Verificar de novo')!.click())
  expect(container.querySelector('progress')?.value).toBe(40)
  expect(button('Cancelar download')?.disabled).toBe(true)
  await act(async () => refreshed.resolve(Response.json(unreachableWithPull)))
  expect(container.querySelector('progress')?.value).toBe(40)
  expect(button('Cancelar download')?.disabled).toBe(false)
  expect(button('Baixar')).toBeUndefined()
})

it.each(['completed', 'failed'] as const)('keeps the %s outcome visible after losing Ollama availability', async (outcome) => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/pulls/p1')
    ? Response.json({ ...pull, status: outcome, finished_at: '2026-11-03T00:01:00Z', phase: outcome === 'completed' ? 'success' : 'downloading', error: outcome === 'failed' ? 'pull_failed' : null })
    : Response.json(unreachableWithPull)))
  await render()
  await vi.waitFor(() => expect(container.textContent).toContain(outcome === 'completed' ? 'qwen3:8b está pronto' : 'O download de qwen3:8b falhou'), { timeout: 2500 })
  expect(container.querySelector('.local-model-pull')).not.toBeNull()
  expect(container.querySelector('progress')).toBeNull()
  expect(button('Baixar')).toBeUndefined()
  expect(button('Remover')).toBeUndefined()
})

it.each(['cancelled', 'completed', 'failed'] as const)('discovers another active download after %s without repeating a mutation', async (outcome) => {
  const next = { ...pull, id: 'p2', model: 'qwen3:4b', completed_bytes: 70 }
  const terminal = { ...pull, status: outcome, finished_at: '2026-11-03T00:01:00Z', phase: outcome === 'completed' ? 'success' : pull.phase, error: outcome === 'failed' ? 'pull_failed' : null }
  let externalStarted = false
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); return Response.json(terminal) }
    if (url.includes('/pulls/p2')) return Response.json(next)
    return Response.json({ ...status, pulls: externalStarted ? [terminal, next] : [pull] })
  }))
  await render()
  await act(async () => button('Cancelar download')!.click())
  expect(container.querySelector('progress')).toBeNull()
  externalStarted = true
  await act(async () => button('Verificar de novo')!.click())
  expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:4b')
  expect(container.querySelector('progress')?.value).toBe(70)
  expect(container.textContent).not.toContain('Download cancelado')
  expect(container.textContent).not.toContain('qwen3:8b está pronto')
  expect(container.textContent).not.toContain('O download de qwen3:8b falhou')
  expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
})

it('keeps tracking its current running download when another one appears first in the catalog', async () => {
  const next = { ...pull, id: 'p2', model: 'qwen3:4b', completed_bytes: 70 }
  let reads = 0
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') writes.push(url)
    reads += 1
    return Response.json({ ...status, pulls: reads === 1 ? [pull] : [next, pull] })
  }))
  await render()
  await act(async () => button('Verificar de novo')!.click())
  expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:8b')
  expect(container.querySelector('progress')?.value).toBe(40)
  expect(writes).toEqual([])
})

it('preserves removal confirmation when its inventory refresh discovers another download', async () => {
  const terminal = { ...pull, status: 'cancelled', finished_at: '2026-11-03T00:01:00Z' }
  const next = { ...pull, id: 'p2', model: 'qwen3:4b', completed_bytes: 70 }
  let removed = false
  const writes: string[] = []
  vi.stubGlobal('confirm', () => true)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      writes.push(url)
      if (url.endsWith('/delete')) { removed = true; return Response.json({ deleted: 'qwen3:1.7b' }) }
      return Response.json(terminal)
    }
    if (url.includes('/pulls/p2')) return Response.json(next)
    return Response.json({ ...status, models: removed ? [] : status.models, pulls: removed ? [terminal, next] : [pull] })
  }))
  await render()
  await act(async () => button('Cancelar download')!.click())
  await act(async () => button('Remover')!.click())
  expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:4b')
  expect(container.textContent).toContain('Modelo removido.')
  expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel', '/api/v1/local-models/delete'])
})

it.each([false, true])('clears a transient poll error after losing tracking (another download: %s)', async (another) => {
  vi.useFakeTimers()
  try {
    let lost = false
    let polls = 0
    const next = { ...pull, id: 'p2', model: 'qwen3:4b', completed_bytes: 70 }
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') writes.push(url)
      if (url.includes('/pulls/p1')) {
        polls += 1
        lost = polls > 1
        return Response.json({ error: { code: lost ? 'pull_not_found' : 'backend_unavailable', message: 'x' } }, { status: lost ? 404 : 503 })
      }
      return Response.json({ ...status, pulls: lost ? (another ? [next] : []) : [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => vi.advanceTimersByTimeAsync(2000))
    expect(container.querySelector('[role="alert"]')).toBeNull()
    if (another) {
      expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:4b')
      expect(container.querySelector('progress')?.value).toBe(70)
      expect(container.textContent).not.toContain('acompanhamento do download de qwen3:8b foi perdido')
    } else {
      expect(container.textContent).toContain('acompanhamento do download de qwen3:8b foi perdido')
      expect(container.querySelector('progress')).toBeNull()
    }
    expect(writes).toEqual([])
  } finally { vi.useRealTimers() }
})

it('starts polling a rediscovered download without inheriting the lost download backoff', async () => {
  vi.useFakeTimers()
  try {
    let lost = false
    let oldPolls = 0
    let newPolls = 0
    const next = { ...pull, id: 'p2', model: 'qwen3:4b', completed_bytes: 70 }
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      expect(options.method).not.toBe('POST')
      if (url.includes('/pulls/p2')) { newPolls += 1; return Response.json(next) }
      if (url.includes('/pulls/p1')) {
        oldPolls += 1
        lost = oldPolls > 1
        return Response.json({ error: { code: lost ? 'pull_not_found' : 'backend_unavailable', message: 'x' } }, { status: lost ? 404 : 503 })
      }
      return Response.json({ ...status, pulls: lost ? [next] : [pull] })
    })
    vi.stubGlobal('fetch', fetcher)
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    await act(async () => vi.advanceTimersByTimeAsync(2000))
    expect(oldPolls).toBe(2)
    expect(newPolls).toBe(0)
    await act(async () => vi.advanceTimersByTimeAsync(999))
    expect(newPolls).toBe(0)
    await act(async () => vi.advanceTimersByTimeAsync(1))
    expect(newPolls).toBe(1)
  } finally { vi.useRealTimers() }
})

it.each(['running', 'cancelled', 'completed', 'failed'] as const)('resets polling backoff after a valid %s cancellation receipt', async (outcome) => {
  vi.useFakeTimers()
  try {
    let recovered = false
    let resumedPolls = 0
    const next = { ...pull, id: 'p2', model: 'qwen3:4b' }
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        writes.push(url); recovered = true
        return Response.json({ ...pull, status: outcome, finished_at: outcome === 'running' ? null : '2026-11-03T00:01:00Z', phase: outcome === 'completed' ? 'success' : pull.phase, error: outcome === 'failed' ? 'pull_failed' : null })
      }
      if (url.includes('/pulls/')) {
        if (recovered) { resumedPolls += 1; return Response.json(outcome === 'running' ? pull : next) }
        return Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 })
      }
      return Response.json({ ...status, pulls: recovered && outcome !== 'running' ? [next] : [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => button('Cancelar download')!.click())
    if (outcome !== 'running' && outcome !== 'completed') await act(async () => button('Verificar de novo')!.click())
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(resumedPolls).toBe(0)
    await act(async () => vi.advanceTimersByTimeAsync(999))
    expect(resumedPolls).toBe(0)
    await act(async () => vi.advanceTimersByTimeAsync(1))
    expect(resumedPolls).toBe(1)
    expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
  } finally { vi.useRealTimers() }
})

it('keeps polling backoff when cancellation fails and never retries the mutation', async () => {
  vi.useFakeTimers()
  try {
    let polls = 0
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') writes.push(url)
      else if (!url.includes('/pulls/')) return Response.json({ ...status, pulls: [pull] })
      else polls += 1
      return Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(polls).toBe(1)
    await act(async () => button('Cancelar download')!.click())
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    await act(async () => vi.advanceTimersByTimeAsync(1999))
    expect(polls).toBe(1)
    await act(async () => vi.advanceTimersByTimeAsync(1))
    expect(polls).toBe(2)
    expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
  } finally { vi.useRealTimers() }
})

it.each([403, 404, 200])('pauses tracking after permanent failure %s and resumes only on human action', async (httpStatus) => {
  vi.useFakeTimers()
  try {
    let polls = 0, recovered = false
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') writes.push(url)
      if (url.endsWith('/pulls/p1')) {
        polls += 1
        return recovered ? Response.json({ ...pull, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success', completed_bytes: 100 })
          : Response.json(httpStatus === 200 ? { ...pull, completed_bytes: -1 } : { error: { code: 'access_denied' } }, { status: httpStatus })
      }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(button('Retomar acompanhamento')).toBeTruthy()
    expect(container.textContent).toContain('o download pode continuar no Ollama')
    expect(container.querySelector('progress')?.value).toBe(40)
    expect(button('Cancelar download')?.disabled).toBe(false)
    expect(button('Baixar')?.disabled).toBe(true)
    await act(async () => vi.advanceTimersByTimeAsync(60000))
    expect(polls).toBe(1)
    await act(async () => button('Verificar de novo')!.click())
    await act(async () => vi.advanceTimersByTimeAsync(60000))
    expect(polls).toBe(1)
    recovered = true
    await act(async () => { button('Retomar acompanhamento')!.click(); button('Retomar acompanhamento')!.click() })
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(polls).toBe(2)
    expect(container.textContent).toContain('qwen3:8b está pronto')
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(button('Retomar acompanhamento')).toBeUndefined()
    expect(container.querySelector('progress')).toBeNull()
    expect(writes).toEqual([])
  } finally { vi.useRealTimers() }
})

it('pauses again after a failed manual read without duplicating requests', async () => {
  vi.useFakeTimers()
  try {
    let polls = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      expect(options.method).toBe('GET')
      if (url.endsWith('/pulls/p1')) { polls += 1; return Response.json({ ...pull, id: 'foreign' }) }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    await act(async () => { button('Retomar acompanhamento')!.click(); button('Retomar acompanhamento')!.click() })
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(polls).toBe(2)
    expect(button('Retomar acompanhamento')).toBeTruthy()
    await act(async () => vi.advanceTimersByTimeAsync(60000))
    expect(polls).toBe(2)
    expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:8b')
  } finally { vi.useRealTimers() }
})

it.each(['cancelled', 'completed', 'failed'] as const)('allows human cancellation while paused and shows canonical %s', async (outcome) => {
  vi.useFakeTimers()
  try {
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') { writes.push(url); return Response.json({ ...pull, status: outcome, finished_at: '2026-11-03T00:01:00Z', phase: outcome === 'completed' ? 'success' : 'downloading', error: outcome === 'failed' ? 'pull_failed' : null }) }
      if (url.endsWith('/pulls/p1')) return Response.json({ error: { code: 'access_denied' } }, { status: 403 })
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(button('Retomar acompanhamento')).toBeTruthy()
    await act(async () => button('Cancelar download')!.click())
    expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
    expect(button('Retomar acompanhamento')).toBeUndefined()
    expect(container.querySelector('progress')).toBeNull()
    expect(container.querySelector('.local-model-pull')?.textContent).toContain(outcome === 'cancelled' ? 'Download cancelado' : outcome === 'completed' ? 'Concluído' : 'O download de qwen3:8b falhou')
  } finally { vi.useRealTimers() }
})

it.each([429, 503, 'network'] as const)('keeps automatic tracking for temporary %s failures', async (outcome) => {
  vi.useFakeTimers()
  try {
    let polls = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      expect(options.method).toBe('GET')
      if (url.endsWith('/pulls/p1')) {
        polls += 1
        if (polls === 1) {
          if (outcome === 'network') throw new TypeError('offline')
          return Response.json({ error: { code: 'backend_unavailable' } }, { status: outcome })
        }
        return Response.json({ ...pull, completed_bytes: 70 })
      }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(button('Retomar acompanhamento')).toBeUndefined()
    await act(async () => vi.advanceTimersByTimeAsync(1999))
    expect(polls).toBe(1)
    await act(async () => vi.advanceTimersByTimeAsync(1))
    expect(polls).toBe(2)
    expect(container.querySelector('progress')?.value).toBe(70)
    expect(container.querySelector('[role="alert"]')).toBeNull()
  } finally { vi.useRealTimers() }
})

it('discards a resumed read after switching endpoint', async () => {
  vi.useFakeTimers()
  try {
    const resumed = deferred<Response>()
    let polls = 0
    const signals: AbortSignal[] = []
    vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
      if (url.endsWith('/pulls/p1')) {
        polls += 1
        if (polls === 1) return Promise.resolve(Response.json({ ...pull, id: 'foreign' }))
        signals.push(options.signal as AbortSignal)
        return resumed.promise
      }
      return Promise.resolve(Response.json(url.includes('11435') ? otherStatus : { ...status, pulls: [pull] }))
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    await act(async () => button('Retomar acompanhamento')!.click())
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    await render(vi.fn(), otherEndpoint)
    expect(signals).toHaveLength(1)
    expect(signals[0].aborted).toBe(true)
    await act(async () => resumed.resolve(Response.json({ ...pull, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success' })))
    expect(container.textContent).toContain('modelo-outro:latest')
    expect(container.textContent).not.toContain('qwen3:8b está pronto')
    expect(button('Retomar acompanhamento')).toBeUndefined()
  } finally { vi.useRealTimers() }
})

it.each(['pt-BR', 'en', 'es'] as const)('offers localized tracking recovery in %s', async (language) => {
  vi.useFakeTimers()
  try {
    i18n.addResourceBundle(language, 'localModel', localModelResources[language])
    i18n.addResourceBundle(language, 'product', productResources[language])
    await i18n.changeLanguage(language)
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/pulls/p1')
      ? Response.json({ ...pull, completed_bytes: -1 }) : Response.json({ ...status, pulls: [pull] })))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    const translations = localModelResources[language]
    expect(container.textContent).toContain(translations.trackingPaused)
    expect(button(translations.resumeTracking)?.disabled).toBe(false)
  } finally { vi.useRealTimers() }
})

it('pauses after malformed HTTP 503 rather than retrying a broken response contract', async () => {
  vi.useFakeTimers()
  try {
    let polls = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/pulls/p1')) { polls += 1; return new Response('not JSON', { status: 503 }) }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(button('Retomar acompanhamento')).toBeTruthy()
    await act(async () => vi.advanceTimersByTimeAsync(60000))
    expect(polls).toBe(1)
  } finally { vi.useRealTimers() }
})

it.each([{ model: 'foreign:latest' }, { endpoint: otherEndpoint }])('preserves confirmed progress and pauses after a divergent identity %j', async (patch) => {
  vi.useFakeTimers()
  try {
    let polls = 0
    let recovered = false
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') writes.push(url)
      if (url.endsWith('/pulls/p1')) {
        polls += 1
        return Response.json(recovered ? { ...pull, completed_bytes: 70 } : { ...pull, ...patch, completed_bytes: 90 })
      }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(button('Retomar acompanhamento')).toBeTruthy()
    expect(container.querySelector('progress')?.value).toBe(40)
    expect(container.querySelector('.local-model-pull')?.textContent).toContain('qwen3:8b')
    expect(container.querySelector('.local-model-pull')?.textContent).not.toContain('foreign')
    await act(async () => vi.advanceTimersByTimeAsync(60000))
    expect(polls).toBe(1)
    await act(async () => button('Verificar de novo')!.click())
    expect(button('Retomar acompanhamento')).toBeTruthy()
    recovered = true
    await act(async () => button('Retomar acompanhamento')!.click())
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(polls).toBe(2)
    expect(container.querySelector('progress')?.value).toBe(70)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(writes).toEqual([])
  } finally { vi.useRealTimers() }
})

it.each([
  { model: 'foreign:latest' }, { endpoint: otherEndpoint },
])('rejects a divergent cancellation receipt without publishing success %j', async (patch) => {
  vi.useFakeTimers()
  try {
    let polls = 0
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        writes.push(url)
        return Response.json({ ...pull, ...patch, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success', completed_bytes: 100 })
      }
      if (url.endsWith('/pulls/p1')) { polls += 1; return Response.json({ ...pull, completed_bytes: 70 }) }
      return Response.json({ ...status, pulls: [pull] })
    }))
    await render()
    await act(async () => button('Cancelar download')!.click())
    expect(container.querySelector('progress')?.value).toBe(40)
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    expect(container.textContent).not.toContain('está pronto')
    expect(container.querySelector('.local-model-pull')?.textContent).not.toContain('foreign')
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(polls).toBe(1)
    expect(container.querySelector('progress')?.value).toBe(70)
    expect(container.querySelector('[role="alert"]')).toBeNull()
    expect(writes).toEqual(['/api/v1/local-models/pulls/p1/cancel'])
  } finally { vi.useRealTimers() }
})

it.each(['read', 'cancel'] as const)('preserves confirmed progress after a contradictory %s receipt', async (operation) => {
  vi.useFakeTimers()
  try {
    let recovered = false
    const writes: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      if (!url.includes('/pulls/')) return Response.json({ ...status, pulls: [pull] })
      if (options.method === 'POST') writes.push(url)
      return Response.json(recovered ? { ...pull, completed_bytes: 70 }
        : { ...pull, status: 'completed', phase: 'downloading', finished_at: '2026-11-03T00:01:00Z' })
    }))
    await render()
    if (operation === 'cancel') await act(async () => button('Cancelar download')!.click())
    else await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('progress')?.getAttribute('value')).toBe('40')
    expect(container.textContent).not.toContain('qwen3:8b está pronto')
    expect(container.querySelector('.local-model-pull')?.textContent).not.toContain('Concluído')
    expect(container.querySelector('[role="alert"]')).not.toBeNull()
    if (operation === 'read') {
      await act(async () => vi.advanceTimersByTimeAsync(30000))
      expect(button('Retomar acompanhamento')).toBeTruthy()
      recovered = true
      await act(async () => button('Retomar acompanhamento')!.click())
    } else recovered = true
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('progress')?.getAttribute('value')).toBe('70')
    expect(writes).toEqual(operation === 'cancel' ? ['/api/v1/local-models/pulls/p1/cancel'] : [])
  } finally { vi.useRealTimers() }
})

it.each([
  ['pt-BR', 'Aguardando confirmação de término…', 'Concluído', 'qwen3:8b está pronto'],
  ['en', 'Waiting for completion confirmation…', 'Done', 'qwen3:8b is ready'],
  ['es', 'Esperando confirmación de finalización…', 'Listo', 'qwen3:8b está listo'],
] as const)('waits for terminal confirmation after success phase in %s', async (language, waiting, done, completed) => {
  vi.useFakeTimers()
  try {
    i18n.addResourceBundle(language, 'localModel', localModelResources[language])
    i18n.addResourceBundle(language, 'product', productResources[language])
    await i18n.changeLanguage(language)
    let terminal = false
    vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/pulls/p1')
      ? Response.json(terminal ? { ...pull, status: 'completed', phase: 'success', finished_at: '2026-11-03T00:01:00Z' } : { ...pull, phase: 'success' })
      : Response.json({ ...status, pulls: [pull] })))
    await render()
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('.local-model-pull')?.textContent).toContain(waiting)
    expect(container.querySelector('.local-model-pull')?.textContent).not.toContain(done)
    expect(container.textContent).not.toContain(completed)
    expect(container.querySelector('progress')).not.toBeNull()
    terminal = true
    await act(async () => vi.advanceTimersByTimeAsync(1000))
    expect(container.querySelector('progress')).toBeNull()
    expect(container.querySelector('.local-model-pull')?.textContent).toContain(done)
    expect(container.textContent).toContain(completed)
    expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
  } finally { vi.useRealTimers() }
})

it.each(['pt-BR', 'en', 'es'] as const)('preserves a lost start across remount and reconciles only by GET in %s', async (language) => {
  i18n.addResourceBundle(language, 'localModel', localModelResources[language]); await i18n.changeLanguage(language)
  const messages = localModelResources[language]
  let intent: Record<string, string> | null = null
  const writes: unknown[] = []
  const reads: string[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      intent = JSON.parse(options.body as string); writes.push(intent)
      const stored = JSON.parse(sessionStorage.getItem(`bees.local-pull-pending.v1.${status.endpoint}`)!)
      expect(stored).toEqual({ endpoint: status.endpoint, model: 'qwen3:8b', clientRequestId: intent!.client_request_id })
      return Response.json({})
    }
    if (url.includes('/pull-requests/')) { reads.push(url); return Response.json({ client_request_id: intent!.client_request_id, pull }) }
    return Response.json(status)
  }))
  await render()
  await act(async () => button(messages.download)!.click())
  expect(writes).toHaveLength(1)
  expect(button(messages.download)?.disabled).toBe(true)
  expect(button(messages.remove)?.disabled).toBe(true)
  await act(async () => root.render(null))
  await render()
  expect(reads).toHaveLength(0)
  expect(writes).toHaveLength(1)
  expect(container.textContent).toContain('qwen3:8b')
  await act(async () => { button(messages.checkRequest)!.click(); button(messages.checkRequest)!.click() })
  expect(reads).toEqual([`/api/v1/local-models/pull-requests/${intent!.client_request_id}`])
  expect(writes).toHaveLength(1)
  expect(button(messages.cancel)).toBeTruthy()
  expect(button(messages.checkRequest)).toBeUndefined()
  expect(sessionStorage.getItem(`bees.local-pull-pending.v1.${status.endpoint}`)).toContain(intent!.client_request_id)
})

it.each(['absent', 'network', 'identity', 'uuid', 'lifecycle'] as const)('keeps an uncertain request after %s lookup without a repeated POST', async (failure) => {
  let requestId = ''
  const writes: string[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); requestId = JSON.parse(options.body as string).client_request_id; return Response.json({}) }
    if (url.includes('/pull-requests/')) {
      if (failure === 'network') throw new Error('private diagnostic')
      if (failure === 'absent') return Response.json({ error: { code: 'pull_not_found', message: 'x' } }, { status: 404 })
      return Response.json({ client_request_id: failure === 'uuid' ? crypto.randomUUID() : requestId, pull: { ...pull, ...(failure === 'identity' ? { model: 'foreign' } : failure === 'lifecycle' ? { status: 'completed' } : {}) } })
    }
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Baixar')!.click())
  for (let n = 0; n < 2; n++) await act(async () => button('Consultar pedido de download')!.click())
  expect(writes).toHaveLength(1)
  expect(button('Consultar pedido de download')).toBeTruthy()
  expect(button('Baixar')?.disabled).toBe(true)
  expect(container.textContent).not.toContain('private diagnostic')
  expect(sessionStorage.getItem(`bees.local-pull-pending.v1.${status.endpoint}`)).toContain(requestId)
  await act(async () => button('Verificar de novo')!.click())
  expect(button('Baixar')?.disabled).toBe(true)
})

it.each(['read', 'write', 'corrupt', 'foreign'] as const)('blocks downloads when recovery storage has %s failure', async (failure) => {
  const key = `bees.local-pull-pending.v1.${status.endpoint}`
  if (failure === 'corrupt') sessionStorage.setItem(key, '{')
  if (failure === 'foreign') sessionStorage.setItem(key, JSON.stringify({ endpoint: 'http://127.0.0.1:11435', model: 'qwen3', clientRequestId: crypto.randomUUID() }))
  if (failure === 'read') vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('private diagnostic') } })
  if (failure === 'write') vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: () => { throw new Error('Quota') } })
  const fetcher = vi.fn(async () => Response.json(status)); vi.stubGlobal('fetch', fetcher); vi.stubGlobal('confirm', vi.fn(() => true))
  await render()
  if (failure === 'write') await act(async () => button('Baixar')!.click())
  expect(container.textContent).toContain('Não foi possível ler ou guardar')
  expect(button('Baixar')?.disabled).toBe(true)
  expect(fetcher.mock.calls).toHaveLength(1)
})

it('requires explicit release and never starts or cancels a download by releasing recovery', async () => {
  const writes: string[] = []
  const confirm = vi.fn().mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); return Response.json({}) }
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Baixar')!.click())
  await act(async () => button('Liberar novo pedido')!.click())
  expect(button('Baixar')?.disabled).toBe(true)
  expect(confirm.mock.calls[1][0]).toContain('não cancela nem comprova')
  await act(async () => button('Liberar novo pedido')!.click())
  expect(button('Baixar')?.disabled).toBe(false)
  expect(writes).toHaveLength(1)
})

it('keeps recovery after a verified receipt when removing storage fails', async () => {
  let requestId = ''
  const key = `bees.local-pull-pending.v1.${status.endpoint}`
  vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: sessionStorage.setItem.bind(sessionStorage), removeItem: () => { throw new Error('blocked') } })
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { requestId = JSON.parse(options.body as string).client_request_id; return Response.json({ ...pull, status: 'completed', phase: 'success', finished_at: '2026-11-03T00:01:00Z' }) }
    if (url.includes('/pull-requests/')) return Response.json({ client_request_id: requestId, pull: { ...pull, status: 'completed', phase: 'success', finished_at: '2026-11-03T00:01:00Z' } })
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Baixar')!.click())
  expect(sessionStorage.getItem(key)).toContain(requestId)
  expect(button('Consultar pedido de download')).toBeTruthy()
  await act(async () => button('Consultar pedido de download')!.click())
  expect(button('Consultar pedido de download')).toBeTruthy()
  expect(button('Cancelar download')).toBeUndefined()
  expect(button('Baixar')?.disabled).toBe(true)
})

it('keeps a pending start on network loss and checks it even when the inventory fails', async () => {
  let requestId = ''; let catalogFails = false
  const writes: string[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); requestId = JSON.parse(options.body as string).client_request_id; throw new Error('lost response') }
    if (url.includes('/pull-requests/')) return Response.json({ client_request_id: requestId, pull: { ...pull, status: 'completed', phase: 'success', finished_at: 'now' } })
    if (catalogFails) throw new Error('inventory lost')
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Baixar')!.click())
  catalogFails = true
  await act(async () => button('Verificar de novo')!.click())
  expect(button('Consultar pedido de download')?.disabled).toBe(false)
  await act(async () => button('Consultar pedido de download')!.click())
  expect(container.textContent).toContain('qwen3:8b está pronto')
  expect(button('Consultar pedido de download')).toBeUndefined()
  expect(writes).toHaveLength(1)
})

it('does not let a late start receipt clear recovery belonging to another endpoint', async () => {
  const receipt = deferred<Response>()
  const otherEndpoint = 'http://127.0.0.1:11435'
  const otherIntent = { endpoint: otherEndpoint, model: 'llama3.2', clientRequestId: crypto.randomUUID() }
  sessionStorage.setItem(`bees.local-pull-pending.v1.${otherEndpoint}`, JSON.stringify(otherIntent))
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (options.method === 'POST') return receipt.promise
    return Promise.resolve(Response.json({ ...status, endpoint: url.includes('11435') ? otherEndpoint : status.endpoint }))
  }))
  await render()
  await act(async () => button('Baixar')!.click())
  await render(vi.fn(), otherEndpoint)
  await act(async () => receipt.resolve(Response.json(pull)))
  expect(sessionStorage.getItem(`bees.local-pull-pending.v1.${otherEndpoint}`)).toBe(JSON.stringify(otherIntent))
  expect(sessionStorage.getItem(`bees.local-pull-pending.v1.${status.endpoint}`)).not.toBeNull()
  expect(button('Consultar pedido de download')).toBeTruthy()
  expect(container.querySelector('.local-model-pull')).toBeNull()
})

it.each(['pt-BR', 'en', 'es'] as const)('preserves uncertain removal after remount and requires explicit release in %s', async (language) => {
  i18n.addResourceBundle(language, 'localModel', localModelResources[language]); await i18n.changeLanguage(language)
  const messages = localModelResources[language]
  const writes: string[] = []
  let absent = false
  const confirm = vi.fn().mockReturnValueOnce(true).mockReturnValueOnce(false).mockReturnValueOnce(true)
  vi.stubGlobal('confirm', confirm)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); throw new Error('lost removal response') }
    return Response.json({ ...status, models: absent ? [] : status.models })
  }))
  await render()
  await act(async () => button(messages.remove)!.click())
  expect(button(messages.download)?.disabled).toBe(true)
  await act(async () => root.unmount()); root = createRoot(container)
  await render()
  expect(button(messages.download)?.disabled).toBe(true)
  expect(button(messages.remove)?.disabled).toBe(true)
  expect(container.textContent).not.toContain(messages.removed)
  absent = true
  await act(async () => button(messages.refresh)!.click())
  expect(button(messages.download)?.disabled).toBe(true)
  // Liberation is a distinct human action, even when the model is absent.
  const release = [...container.querySelectorAll('button')].find((item) => item.textContent === messages.releaseRemoval)!
  await act(async () => release.click())
  expect(button(messages.download)?.disabled).toBe(true)
  await act(async () => release.click())
  expect(button(messages.download)?.disabled).toBe(false)
  expect(confirm.mock.calls[1][0]).toBe(messages.releaseRemovalConfirm)
  expect(writes).toEqual(['/api/v1/local-models/delete'])
})

it.each(['invalid', 'rejected'] as const)('retains removal after %s response and a failed inventory read', async (failure) => {
  let catalogFails = false
  const writes: string[] = []
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      writes.push(url)
      expect(JSON.parse(sessionStorage.getItem(`bees.local-removal-pending.v1.${status.endpoint}`)!)).toEqual({ endpoint: status.endpoint, model: 'qwen3:1.7b' })
      return failure === 'invalid' ? Response.json({ deleted: 'another:latest' }) : Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 })
    }
    if (catalogFails) throw new Error('catalog failed')
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Remover')!.click())
  expect(button('Baixar')?.disabled).toBe(true)
  catalogFails = true
  await act(async () => button('Verificar de novo')!.click())
  expect(button('Liberar após remoção pendente')?.disabled).toBe(false)
  expect(container.textContent).not.toContain('Modelo removido.')
  expect(writes).toEqual(['/api/v1/local-models/delete'])
})

it('clears a valid removal receipt and permits a separately confirmed download', async () => {
  const writes: string[] = []
  const confirm = vi.fn(() => true); vi.stubGlobal('confirm', confirm)
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      writes.push(url)
      return Response.json(url.endsWith('/delete') ? { deleted: 'qwen3:1.7b' } : pull)
    }
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Remover')!.click())
  expect(sessionStorage.getItem(`bees.local-removal-pending.v1.${status.endpoint}`)).toBeNull()
  expect(button('Liberar após remoção pendente')).toBeUndefined()
  await act(async () => button('Baixar')!.click())
  expect(writes).toEqual(['/api/v1/local-models/delete', '/api/v1/local-models/pulls'])
  expect(confirm).toHaveBeenCalledTimes(2)
})

it.each(['corrupt', 'foreign', 'read', 'write'] as const)('blocks removals and downloads when removal storage has %s failure', async (failure) => {
  const key = `bees.local-removal-pending.v1.${status.endpoint}`
  if (failure === 'corrupt') sessionStorage.setItem(key, '{}')
  if (failure === 'foreign') sessionStorage.setItem(key, JSON.stringify({ endpoint: 'http://127.0.0.1:11435', model: 'qwen3' }))
  if (failure === 'read') vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('private storage diagnostic') } })
  if (failure === 'write') vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: () => { throw new Error('Quota') } })
  const fetcher = vi.fn(async () => Response.json(status)); vi.stubGlobal('fetch', fetcher); vi.stubGlobal('confirm', vi.fn(() => true))
  await render()
  if (failure === 'write') await act(async () => button('Remover')!.click())
  expect(button('Remover')?.disabled).toBe(true)
  expect(button('Baixar')?.disabled).toBe(true)
  expect(container.textContent).toContain('Não foi possível ler ou guardar o pedido de remoção')
  expect(container.textContent).not.toContain('private storage diagnostic')
  expect(fetcher.mock.calls).toHaveLength(1)
})

it('keeps a confirmed removal blocked when clearing storage fails, including explicit release', async () => {
  const key = `bees.local-removal-pending.v1.${status.endpoint}`
  const writes: string[] = []
  vi.stubGlobal('sessionStorage', { getItem: sessionStorage.getItem.bind(sessionStorage), setItem: sessionStorage.setItem.bind(sessionStorage), removeItem: () => { throw new Error('blocked') } })
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(url); return Response.json({ deleted: 'qwen3:1.7b' }) }
    return Response.json(status)
  }))
  await render()
  await act(async () => button('Remover')!.click())
  expect(container.textContent).toContain('Modelo removido.')
  expect(button('Baixar')?.disabled).toBe(true)
  await act(async () => button('Liberar após remoção pendente')!.click())
  expect(button('Baixar')?.disabled).toBe(true)
  expect(sessionStorage.getItem(key)).not.toBeNull()
  expect(writes).toEqual(['/api/v1/local-models/delete'])
})

it('does not persist or send a removal when confirmation is declined', async () => {
  vi.stubGlobal('confirm', vi.fn(() => false))
  const fetcher = vi.fn(async () => Response.json(status)); vi.stubGlobal('fetch', fetcher)
  await render()
  await act(async () => button('Remover')!.click())
  expect(sessionStorage.getItem(`bees.local-removal-pending.v1.${status.endpoint}`)).toBeNull()
  expect(fetcher.mock.calls).toHaveLength(1)
  expect(button('Baixar')?.disabled).toBe(false)
})

it('preserves uncertain removal on exit and ignores its late receipt at a different endpoint', async () => {
  const receipt = deferred<Response>()
  const otherEndpoint = 'http://127.0.0.1:11435'
  const otherIntent = { endpoint: otherEndpoint, model: 'llama3.2' }
  sessionStorage.setItem(`bees.local-removal-pending.v1.${otherEndpoint}`, JSON.stringify(otherIntent))
  vi.stubGlobal('confirm', vi.fn(() => true))
  vi.stubGlobal('fetch', vi.fn((url: string, options: RequestInit) => {
    if (options.method === 'POST') return receipt.promise
    return Promise.resolve(Response.json({ ...status, endpoint: url.includes('11435') ? otherEndpoint : status.endpoint }))
  }))
  await render()
  await act(async () => button('Remover')!.click())
  await render(vi.fn(), otherEndpoint)
  await act(async () => receipt.resolve(Response.json({ deleted: 'qwen3:1.7b' })))
  expect(sessionStorage.getItem(`bees.local-removal-pending.v1.${otherEndpoint}`)).toBe(JSON.stringify(otherIntent))
  expect(sessionStorage.getItem(`bees.local-removal-pending.v1.${status.endpoint}`)).not.toBeNull()
  expect(container.textContent).not.toContain('Modelo removido.')
  await render()
  expect(button('Baixar')?.disabled).toBe(true)
})

it.each(['Remover', 'Baixar'])('rechecks stored removal after awaiting confirmation of %s', async (action) => {
  const decision = deferred<boolean>()
  registerConfirmHost((request) => { void decision.promise.then(request.resolve) })
  const fetcher = vi.fn(async () => Response.json(status)); vi.stubGlobal('fetch', fetcher)
  await render()
  await act(async () => button(action)!.click())
  sessionStorage.setItem(`bees.local-removal-pending.v1.${status.endpoint}`, JSON.stringify({ endpoint: status.endpoint, model: 'other:latest' }))
  await act(async () => decision.resolve(true))
  expect(button('Liberar após remoção pendente')?.disabled).toBe(false)
  expect(button('Baixar')?.disabled).toBe(true)
  expect(button('Remover')?.disabled).toBe(true)
  expect(fetcher.mock.calls).toHaveLength(1)
})

it('keeps download recovery independent when a pending removal is released', async () => {
  const endpoint = status.endpoint
  const download = { endpoint, model: pull.model, clientRequestId: crypto.randomUUID() }
  sessionStorage.setItem(`bees.local-pull-pending.v1.${endpoint}`, JSON.stringify(download))
  sessionStorage.setItem(`bees.local-removal-pending.v1.${endpoint}`, JSON.stringify({ endpoint, model: 'qwen3:1.7b' }))
  vi.stubGlobal('confirm', vi.fn(() => true)); vi.stubGlobal('fetch', vi.fn(async () => Response.json(status)))
  await render()
  await act(async () => button('Liberar após remoção pendente')!.click())
  expect(button('Baixar')?.disabled).toBe(true)
  expect(button('Consultar pedido de download')).toBeTruthy()
  expect(sessionStorage.getItem(`bees.local-pull-pending.v1.${endpoint}`)).toBe(JSON.stringify(download))
  expect(sessionStorage.getItem(`bees.local-removal-pending.v1.${endpoint}`)).toBeNull()
})

it.each(['', `${status.endpoint}/`])('recognizes pending removal at equivalent endpoint %s and isolates other destinations', async (equivalent) => {
  sessionStorage.setItem(`bees.local-removal-pending.v1.${status.endpoint}`, JSON.stringify({ endpoint: '', model: 'qwen3:1.7b' }))
  const otherEndpoint = 'http://127.0.0.1:11435'
  vi.stubGlobal('fetch', vi.fn(async (url: string) => Response.json({ ...status, endpoint: url.includes('11435') ? otherEndpoint : status.endpoint })))
  await render(vi.fn(), equivalent)
  expect(button('Baixar')?.disabled).toBe(true)
  await render(vi.fn(), otherEndpoint)
  expect(button('Baixar')?.disabled).toBe(false)
  expect(button('Remover')?.disabled).toBe(false)
  await render(vi.fn(), equivalent)
  expect(button('Baixar')?.disabled).toBe(true)
  expect(vi.mocked(fetch).mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
})
