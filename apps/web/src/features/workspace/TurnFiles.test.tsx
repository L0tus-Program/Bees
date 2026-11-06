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

it('shows only ready files with the current download URL', async () => {
  vi.mocked(getArtifacts).mockResolvedValue({ ...page(), items: [artifact, { ...artifact, id: 'draft', name: 'rascunho.txt', status: 'draft' }] })
  await show()
  const link = container.querySelector('a')!
  expect(link.getAttribute('href')).toBe(`/api/v1/agents/${agentId}/artifacts/${artifact.id}/download`)
  expect(link.getAttribute('download')).toBe(artifact.name)
  expect(container.textContent).not.toContain('rascunho.txt')
})
for (const field of ['id', 'agent_id', 'revision'] as const) {
  const changed = () => ({ ...task, [field]: field === 'revision' ? task.revision + 1 : '99999999-9999-4999-8999-999999999999' })
  it(`hides the old snapshot during a pending ${field} change`, async () => {
    await show()
    const next = pending(); vi.mocked(getArtifacts).mockReturnValueOnce(next.promise)
    await show(changed())
    expect(container.querySelector('a')).toBeNull()
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
  expect(container.querySelector('a')).toBeNull()
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
  expect(container.querySelector('a')).not.toBeNull()
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
  expect(container.querySelector('a')).not.toBeNull()
})
