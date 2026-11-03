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

it('lists installed models and selects one', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json(status)))
  const onSelect = await render()
  await vi.waitFor(() => expect(container.textContent).toContain('Ollama conectado (versão 0.40.6)'))
  await act(async () => button('Usar este modelo')!.click())
  expect(onSelect).toHaveBeenCalledWith('qwen3:1.7b')
})

it('asks for confirmation with size and source before downloading, then follows progress', async () => {
  const writes: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { writes.push(JSON.parse(options.body as string)); return Response.json(pull, { status: 202 }) }
    if (url.includes('/pulls/p1')) return Response.json({ ...pull, status: 'completed', phase: 'success', completed_bytes: 100 })
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
    if (url.includes('/pulls/p1')) { reads += 1; return reads === 1 ? Response.json({ error: { code: 'backend_unavailable', message: 'x' } }, { status: 503 }) : Response.json({ ...pull, status: 'completed', phase: 'success', completed_bytes: 100 }) }
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
  await act(async () => poll.resolve(Response.json({ ...pull, status: 'completed', phase: 'success' })))
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
    if (options.method === 'POST') { writes.push(url); return Response.json({ ...pull, status: 'cancelled' }) }
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
      return Response.json({ ...pull, status: outcome, phase: outcome === 'completed' ? 'success' : 'downloading', error: outcome === 'failed' ? 'pull_failed' : null })
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
    if (options.method === 'POST') { writes.push(url); return Response.json({ ...pull, status: 'cancelled' }) }
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
    ? Response.json({ ...pull, status: outcome, phase: outcome === 'completed' ? 'success' : 'downloading', error: outcome === 'failed' ? 'pull_failed' : null })
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
  const terminal = { ...pull, status: outcome, phase: outcome === 'completed' ? 'success' : pull.phase, error: outcome === 'failed' ? 'pull_failed' : null }
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
  const terminal = { ...pull, status: 'cancelled' }
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
