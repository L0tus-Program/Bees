// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { workspaceResources } from '../../locales/workspace'
import { artifactResources } from '../../locales/artifacts'
import { getArtifacts } from '../artifacts/api'
import { artifact, agentId, taskId, binary } from '../artifacts/fixtures'
import { task as baseTask } from '../chat/testFixtures'
import { TurnFiles } from './AgentTurn'

// Keep the real download SDK: these tests prove the inline action checks bytes before clicking.
vi.mock('../artifacts/api', async (original) => ({ ...(await original<typeof import('../artifacts/api')>()), getArtifacts: vi.fn() }))
const task = { ...baseTask, id: taskId, agent_id: agentId }
let root: Root, container: HTMLDivElement, i18n: ReturnType<typeof createInstance>
let fetcher: ReturnType<typeof vi.fn>, click: ReturnType<typeof vi.spyOn>
const urls = { createObjectURL: vi.fn(), revokeObjectURL: vi.fn() }
async function show(current = task) {
  await act(async () => root.render(<I18nextProvider i18n={i18n}><TurnFiles task={current} /></I18nextProvider>))
}
const buttons = () => [...container.querySelectorAll('button')]
async function settle() { await vi.waitFor(async () => { await act(async () => {}); expect(container.querySelector('[aria-busy="true"]')).toBeNull() }) }
function pending() {
  let resolve!: (value: Response) => void, reject!: (reason: Error) => void
  const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance()
  await i18n.init({ lng: 'pt-BR', resources: Object.fromEntries(['pt-BR', 'en', 'es'].map((lng) => [lng, {
    workspace: workspaceResources[lng as keyof typeof workspaceResources], artifacts: artifactResources[lng as keyof typeof artifactResources],
  }])), interpolation: { escapeValue: false } })
  vi.mocked(getArtifacts).mockResolvedValue({ items: [artifact, { ...artifact, id: '55555555-5555-4555-8555-555555555555', name: 'outro.txt' }], has_more: false, next_offset: null })
  fetcher = vi.fn().mockImplementation(async () => binary()); vi.stubGlobal('fetch', fetcher)
  vi.spyOn(URL, 'createObjectURL').mockImplementation(urls.createObjectURL.mockReturnValue('blob:verified'))
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(urls.revokeObjectURL)
  click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
})
afterEach(async () => {
  await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.resetAllMocks()
})
it('uses authenticated exact-version SDK and only clicks a verified blob, preserving focus', async () => {
  await show()
  expect(container.querySelector('a')).toBeNull()
  const composer = document.createElement('textarea'); container.before(composer); composer.focus()
  await act(async () => buttons()[0].click()); await settle()
  expect(fetcher).toHaveBeenCalledWith(`/api/v1/agents/${agentId}/artifacts/${artifact.id}/download`, expect.objectContaining({ credentials: 'same-origin', redirect: 'error', cache: 'no-store', headers: { Accept: artifact.media_type } }))
  expect(click).toHaveBeenCalledTimes(1)
  const anchor = click.mock.instances[0] as HTMLAnchorElement
  expect(anchor.download).toBe(artifact.name); expect(anchor.href).toBe('blob:verified'); expect(anchor.isConnected).toBe(false)
  expect(urls.createObjectURL.mock.calls[0][0].type).toBe('application/octet-stream')
  expect(await urls.createObjectURL.mock.calls[0][0].text()).toBe('abc')
  expect(container.querySelector('[role="status"]')?.textContent).toBe(artifactResources['pt-BR'].sent)
  expect(document.activeElement).toBe(composer); composer.remove()
})
it('serializes duplicate and other-file clicks before React commits disabled buttons', async () => {
  const next = pending(); fetcher.mockReturnValueOnce(next.promise)
  await show(); const [first, second] = buttons()
  await act(async () => { first.click(); first.click(); second.click() })
  expect(fetcher).toHaveBeenCalledTimes(1); expect(buttons().every((button) => button.disabled)).toBe(true)
  expect(container.querySelector('[role="status"]')?.textContent).toBe(artifactResources['pt-BR'].downloading)
  await act(async () => next.resolve(binary())); await settle()
  expect(buttons().every((button) => !button.disabled)).toBe(true)
})
for (const lng of ['pt-BR', 'en', 'es'] as const) it(`reports integrity failure and allows only human retry (${lng})`, async () => {
  await i18n.changeLanguage(lng); fetcher.mockResolvedValueOnce(binary('xyz'))
  await show(); await act(async () => buttons()[0].click()); await settle()
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(artifactResources[lng].integrityError)
  expect(click).not.toHaveBeenCalled(); expect(urls.createObjectURL).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1)
  const next = pending(); fetcher.mockReturnValueOnce(next.promise)
  await act(async () => buttons()[0].click())
  expect(container.querySelector('[role="alert"]')).toBeNull()
  await act(async () => next.resolve(binary())); await settle()
  expect(container.querySelector('[role="status"]')?.textContent).toBe(artifactResources[lng].sent)
  expect(fetcher).toHaveBeenCalledTimes(2)
})
for (const [response, error] of [
  [() => binary('ab'), 'integrityError'], [() => binary('abcd'), 'integrityError'],
  [() => binary('abc', { 'Content-Disposition': 'inline' }), 'downloadError'],
  [() => binary('abc', { 'Content-Type': 'text/html' }), 'downloadError'],
  [() => Response.json({ error: { code: 'artifact_blob_missing' } }, { status: 409 }), 'integrityError'],
  [() => Response.json({ error: { code: 'artifact_not_ready' } }, { status: 409 }), 'notReadyError'],
] as const) it(`does not download invalid bytes, metadata or unavailable files (${error})`, async () => {
  fetcher.mockResolvedValueOnce(response()); await show(); await act(async () => buttons()[0].click()); await settle()
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(artifactResources['pt-BR'][error])
  expect(click).not.toHaveBeenCalled(); expect(urls.createObjectURL).not.toHaveBeenCalled()
})
it('expires the session on 401 without navigating or exposing response text', async () => {
  const expired = vi.fn(); window.addEventListener('bees:session-expired', expired)
  try {
    fetcher.mockResolvedValueOnce(Response.json({ error: { code: 'session_expired' }, secret: 'private-path' }, { status: 401 }))
    await show(); await act(async () => buttons()[0].click()); await settle()
    expect(expired).toHaveBeenCalledTimes(1); expect(click).not.toHaveBeenCalled()
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(artifactResources['pt-BR'].downloadError)
    expect(container.textContent).not.toContain('private-path')
  } finally { window.removeEventListener('bees:session-expired', expired) }
})
it('rejects oversized files before network and releases the operation lock', async () => {
  vi.mocked(getArtifacts).mockResolvedValueOnce({ items: [{ ...artifact, size_bytes: 26 * 1024 * 1024 }], has_more: false, next_offset: null })
  await show(); await act(async () => buttons()[0].click()); await settle()
  expect(fetcher).not.toHaveBeenCalled(); expect(click).not.toHaveBeenCalled()
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(artifactResources['pt-BR'].sizeError)
  expect(buttons()[0].disabled).toBe(false)
})
for (const change of ['agent', 'task', 'revision', 'status', 'unmount'] as const) {
  for (const outcome of ['success', 'failure'] as const) it(`ignores late download ${outcome} after ${change} even if transport ignores abort`, async () => {
    const old = pending(); fetcher.mockReturnValueOnce(old.promise)
    await show(); await act(async () => buttons()[0].click())
    const signal = fetcher.mock.calls[0][1].signal as AbortSignal
    if (change === 'unmount') { await act(async () => root.unmount()); root = createRoot(container) }
    else await show({ ...task, ...(change === 'agent' ? { agent_id: 'other' } : change === 'task' ? { id: 'other' } : change === 'revision' ? { revision: task.revision + 1 } : { status: 'running' as const }) })
    expect(signal.aborted).toBe(true)
    await act(async () => { if (outcome === 'success') old.resolve(binary()); else old.reject(new Error('private-path')) })
    expect(click).not.toHaveBeenCalled(); expect(urls.createObjectURL).not.toHaveBeenCalled()
    expect(container.querySelector('[role="alert"]')).toBeNull(); expect(container.textContent).not.toContain(artifactResources['pt-BR'].sent)
  })
}
it('releases the blob immediately if the browser handoff throws and allows retry', async () => {
  click.mockImplementationOnce(() => { throw new Error('private browser error') })
  await show(); await act(async () => buttons()[0].click()); await settle()
  expect(urls.revokeObjectURL).toHaveBeenCalledWith('blob:verified'); expect(document.querySelector('a[download]')).toBeNull()
  expect(container.querySelector('[role="alert"]')?.textContent).toBe(artifactResources['pt-BR'].downloadError)
  await act(async () => buttons()[0].click()); await settle(); expect(click).toHaveBeenCalledTimes(2)
})
it('releases successful blob URLs after 30 seconds and cancels their timers on snapshot replacement', async () => {
  await show(); vi.useFakeTimers()
  await act(async () => buttons()[0].click())
  // WebCrypto settles outside the fake clock.
  await settle(); expect(click).toHaveBeenCalledTimes(1)
  expect(urls.revokeObjectURL).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(30000))
  expect(urls.revokeObjectURL).toHaveBeenCalledTimes(1)
  await act(async () => buttons()[0].click()); await settle(); expect(click).toHaveBeenCalledTimes(2)
  await show({ ...task, revision: task.revision + 1 })
  expect(urls.revokeObjectURL).toHaveBeenCalledTimes(2)
  await vi.advanceTimersByTimeAsync(30000); expect(urls.revokeObjectURL).toHaveBeenCalledTimes(2)
})
