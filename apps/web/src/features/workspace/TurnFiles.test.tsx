// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { ApiError } from '../../api/client'
import { workspaceResources } from '../../locales/workspace'
import { productResources } from '../../locales/product'
import { getArtifacts } from '../artifacts/api'
import type { ArtifactPage } from '../artifacts/api'
import { artifact, agentId, taskId } from '../artifacts/fixtures'
import { task as baseTask } from '../chat/testFixtures'
import { TurnFiles } from './AgentTurn'

vi.mock('../artifacts/api', async (original) => ({ ...(await original<typeof import('../artifacts/api')>()), getArtifacts: vi.fn() }))
const task = { ...baseTask, id: taskId, agent_id: agentId }
const page = (name = 'atual.txt'): ArtifactPage => ({ items: [{ ...artifact, name }], has_more: false, next_offset: null })
function pending() {
  let resolve!: (value: ArtifactPage) => void, reject!: (reason: Error) => void
  const promise = new Promise<ArtifactPage>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
let root: Root, container: HTMLDivElement, i18n: ReturnType<typeof createInstance>
async function show(current = task) {
  await act(async () => root.render(<I18nextProvider i18n={i18n}><TurnFiles task={current} /></I18nextProvider>))
}
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: Object.fromEntries(['pt-BR', 'en', 'es'].map((lng) => [lng, {
    workspace: workspaceResources[lng as keyof typeof workspaceResources], product: productResources[lng as keyof typeof productResources],
  }])), interpolation: { escapeValue: false } })
  vi.mocked(getArtifacts).mockResolvedValue(page())
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks() })

it('shows only ready files as verified download actions', async () => {
  vi.mocked(getArtifacts).mockResolvedValue({ ...page(), items: [artifact, { ...artifact, id: 'draft', name: 'rascunho.txt', status: 'draft' }] })
  await show()
  const button = container.querySelector('button')!
  expect(button.getAttribute('aria-label')).toBe(`Baixar ${artifact.name}`)
  expect(container.querySelector('a')).toBeNull()
  expect(container.textContent).not.toContain('rascunho.txt')
})
for (const field of ['id', 'agent_id', 'revision'] as const) {
  const changed = () => ({ ...task, [field]: field === 'revision' ? task.revision + 1 : '99999999-9999-4999-8999-999999999999' })
  it(`hides the old snapshot during a pending ${field} change`, async () => {
    await show()
    const next = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(next.promise)
    await show(changed())
    expect(container.querySelector('button')).toBeNull()
    expect(getArtifacts).toHaveBeenCalledTimes(2)
    await act(async () => next.resolve(page('novo.txt')))
    expect(container.textContent).toContain('novo.txt')
  })
  for (const outcome of ['success', 'failure'] as const) it(`ignores late ${outcome} after ${field} changes, even if transport ignores abort`, async () => {
    const old = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(old.promise)
    await show()
    const signal = vi.mocked(getArtifacts).mock.calls[0][3]!
    await show(changed())
    expect(signal.aborted).toBe(true)
    await act(async () => { if (outcome === 'success') old.resolve(page('antigo.txt')); else old.reject(new ApiError('network_error')) })
    expect(container.textContent).toContain('atual.txt')
    expect(container.textContent).not.toContain('antigo.txt')
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })
}
for (const status of ['running', 'failed', 'cancelled', 'waiting_approval'] as const) it(`hides files and aborts when status becomes ${status}`, async () => {
  await show()
  const old = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(old.promise)
  await show({ ...task, id: 'other' })
  const signal = vi.mocked(getArtifacts).mock.calls[1][3]!
  await show({ ...task, id: 'other', status })
  expect(signal.aborted).toBe(true)
  await act(async () => old.resolve(page('antigo.txt')))
  expect(container.querySelector('button')).toBeNull()
  expect(getArtifacts).toHaveBeenCalledTimes(2)
})
it('refreshes on completed/paused and revisions without refetching identical renders', async () => {
  await show(); await show()
  expect(getArtifacts).toHaveBeenCalledTimes(1)
  await show({ ...task, status: 'paused' })
  expect(getArtifacts).toHaveBeenCalledTimes(2)
  await show({ ...task, status: 'paused', revision: task.revision + 1 })
  expect(getArtifacts).toHaveBeenCalledTimes(3)
})
for (const lng of ['pt-BR', 'en', 'es'] as const) it(`offers translated human retry on query failure (${lng})`, async () => {
  await i18n.changeLanguage(lng)
  vi.mocked(getArtifacts).mockRejectedValueOnce(new ApiError('network_error'))
  const composer = document.createElement('textarea'); container.before(composer); composer.focus()
  await show()
  expect(document.activeElement).toBe(composer); composer.remove()
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(workspaceResources[lng].filesLoadError)
  const button = container.querySelector('button')!
  expect(button?.textContent).toBe(workspaceResources[lng].retry)
  expect(getArtifacts).toHaveBeenCalledTimes(1)
  const next = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(next.promise)
  await act(async () => button.click())
  expect(container.querySelector('button')).toBeNull()
  expect(container.querySelector('[role="status"]')?.textContent).toBe(workspaceResources[lng].loadingFiles)
  await act(async () => next.resolve(page()))
  expect(container.querySelector('[role="alert"]')).toBeNull()
  expect(container.querySelector('button')).not.toBeNull()
  expect(getArtifacts).toHaveBeenCalledTimes(2)
})
for (const outcome of ['success', 'failure'] as const) it(`aborts on unmount and ignores late ${outcome}`, async () => {
  const old = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(old.promise)
  await show()
  const signal = vi.mocked(getArtifacts).mock.calls[0][3]!
  await act(async () => root.unmount()); root = createRoot(container)
  expect(signal.aborted).toBe(true)
  await act(async () => { if (outcome === 'success') old.resolve(page()); else old.reject(new ApiError('network_error')) })
  expect(container.textContent).toBe('')
  expect(getArtifacts).toHaveBeenCalledTimes(1)
})

it('hides a previous failure when switching to a new pending task', async () => {
  vi.mocked(getArtifacts).mockRejectedValueOnce(new ApiError('network_error'))
  await show()
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  const next = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(next.promise)
  await show({ ...task, id: 'other' })
  expect(container.querySelector('[role="alert"]')).toBeNull()
  await act(async () => next.resolve(page()))
  expect(container.querySelector('button')).not.toBeNull()
})

function fullPage(status: 'ready' | 'draft' = 'ready'): ArtifactPage {
  return { items: Array.from({ length: 100 }, (_, index) => ({ ...artifact, id: `55555555-5555-4555-8555-${String(index).padStart(12, '0')}`, name: `arquivo-${index}.txt`, status })), has_more: true, next_offset: 100 }
}
function moreButton() {
  return [...container.querySelectorAll('button')].find((button) => button.textContent === workspaceResources[i18n.language as keyof typeof workspaceResources].moreFiles)!
}
for (const lng of ['pt-BR', 'en', 'es'] as const) it(`loads the next page only on demand and stops at the end (${lng})`, async () => {
  await i18n.changeLanguage(lng)
  vi.mocked(getArtifacts).mockResolvedValueOnce(fullPage()).mockResolvedValueOnce(page('final.txt'))
  await show()
  expect(getArtifacts).toHaveBeenCalledTimes(1)
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(100)
  expect(moreButton().textContent).toBe(workspaceResources[lng].moreFiles)
  await act(async () => moreButton().click())
  expect(getArtifacts).toHaveBeenLastCalledWith(agentId, taskId, 100, expect.any(AbortSignal))
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(101)
  expect(container.textContent).toContain('arquivo-0.txt')
  expect(container.textContent).toContain('final.txt')
  expect(moreButton()).toBeUndefined()
})
it('keeps the cursor from pages containing no ready artifacts', async () => {
  vi.mocked(getArtifacts).mockResolvedValueOnce(fullPage('draft')).mockResolvedValueOnce(page('entrega.txt'))
  await show()
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(0)
  expect(getArtifacts).toHaveBeenCalledTimes(1)
  await act(async () => moreButton().click())
  expect(getArtifacts).toHaveBeenLastCalledWith(agentId, taskId, 100, expect.any(AbortSignal))
  expect(container.textContent).toContain('entrega.txt')
})
it('serializes same-event clicks while retaining files and focus during loading', async () => {
  const next = pending()
  vi.mocked(getArtifacts).mockResolvedValueOnce(fullPage()).mockReturnValueOnce(next.promise)
  await show()
  const button = moreButton(); button.focus()
  await act(async () => { button.click(); button.click() })
  expect(getArtifacts).toHaveBeenCalledTimes(2)
  expect(moreButton().disabled).toBe(true)
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(100)
  expect(container.querySelector('[role="status"]')?.textContent).toBe(workspaceResources['pt-BR'].loadingFiles)
  expect(document.activeElement).toBe(button)
  await act(async () => next.resolve({ ...fullPage(), items: page('segunda.txt').items, next_offset: 200 }))
  expect(moreButton().disabled).toBe(false)
  expect(document.activeElement).toBe(button)
  await act(async () => moreButton().click())
  expect(getArtifacts).toHaveBeenLastCalledWith(agentId, taskId, 200, expect.any(AbortSignal))
})
it('preserves loaded files after failure and retries only the pending page with a synchronous lock', async () => {
  vi.mocked(getArtifacts).mockResolvedValueOnce(fullPage()).mockRejectedValueOnce(new ApiError('network_error'))
  await show(); await act(async () => moreButton().click())
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(100)
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(workspaceResources['pt-BR'].filesLoadError)
  expect(getArtifacts).toHaveBeenCalledTimes(2)
  const next = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(next.promise)
  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === workspaceResources['pt-BR'].retry)!
  await act(async () => { retry.click(); retry.click() })
  expect(getArtifacts).toHaveBeenCalledTimes(3)
  expect(getArtifacts).toHaveBeenLastCalledWith(agentId, taskId, 100, expect.any(AbortSignal))
  expect(container.querySelector('[role="alert"]')).toBeNull()
  await act(async () => next.resolve(page('recuperado.txt')))
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(101)
})
it('merges overlapping IDs without duplicate download actions', async () => {
  const first = fullPage()
  vi.mocked(getArtifacts).mockResolvedValueOnce(first).mockResolvedValueOnce({ ...page(), items: [first.items[0], ...page().items] })
  await show(); await act(async () => moreButton().click())
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(101)
  expect(container.querySelectorAll('[aria-label="Baixar arquivo-0.txt"]')).toHaveLength(1)
})
it('rejects incompatible overlapping metadata without replacing existing downloads', async () => {
  const first = fullPage()
  vi.mocked(getArtifacts).mockResolvedValueOnce(first).mockResolvedValueOnce({ ...page(), items: [{ ...first.items[0], name: 'alterado.txt' }] })
  await show(); await act(async () => moreButton().click())
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(100)
  expect(container.textContent).not.toContain('alterado.txt')
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
})
for (const change of ['agent', 'task', 'revision', 'status', 'unmount'] as const) {
  for (const outcome of ['success', 'failure'] as const) it(`ignores late next-page ${outcome} after ${change}`, async () => {
    const old = pending()
    vi.mocked(getArtifacts).mockResolvedValueOnce(fullPage()).mockReturnValueOnce(old.promise)
    await show(); await act(async () => moreButton().click())
    const signal = vi.mocked(getArtifacts).mock.calls[1][3]!
    if (change === 'unmount') { await act(async () => root.unmount()); root = createRoot(container) }
    else await show({ ...task, ...(change === 'agent' ? { agent_id: 'other' } : change === 'task' ? { id: 'other' } : change === 'revision' ? { revision: task.revision + 1 } : { status: 'running' as const }) })
    expect(signal.aborted).toBe(true)
    await act(async () => { if (outcome === 'success') old.resolve(page('obsoleto.txt')); else old.reject(new ApiError('network_error')) })
    expect(container.textContent).not.toContain('obsoleto.txt')
    expect(container.textContent).not.toContain('arquivo-0.txt')
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })
}

it('uses the real authenticated SDK for 100 plus one files and a failed-page retry', async () => {
  const sdk = await vi.importActual<typeof import('../artifacts/api')>('../artifacts/api')
  vi.mocked(getArtifacts).mockImplementation(sdk.getArtifacts)
  const first = fullPage()
  const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ artifacts: first.items, has_more: true, next_offset: 100 }))
    .mockResolvedValueOnce(Response.json({ error: { code: 'temporary_unavailable' } }, { status: 503 }))
    .mockResolvedValueOnce(Response.json({ artifacts: page('ultimo.txt').items, has_more: false, next_offset: null }))
  vi.stubGlobal('fetch', fetcher)
  await show()
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => moreButton().click())
  expect(container.querySelector('[role="alert"]')).not.toBeNull()
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(100)
  const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === workspaceResources['pt-BR'].retry)!
  await act(async () => retry.click())
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual([0, 100, 100].map((offset) => `/api/v1/agents/${agentId}/tasks/${taskId}/artifacts?offset=${offset}&limit=100`))
  for (const [, options] of fetcher.mock.calls) expect(options).toMatchObject({ credentials: 'same-origin', cache: 'no-store', signal: expect.any(AbortSignal) })
  expect(container.querySelectorAll('button[aria-label]')).toHaveLength(101)
  expect(moreButton()).toBeUndefined()
  expect(container.querySelector('[role="alert"]')).toBeNull()
})
