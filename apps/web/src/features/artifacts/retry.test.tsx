// @vitest-environment happy-dom
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactResources } from '../../locales/artifacts'
import { ConversationArtifacts } from './ConversationArtifacts'
import { TaskArtifacts } from './TaskArtifacts'
import { agentId, artifact, conversationId, page, taskId } from './fixtures'

let root: Root
let container: HTMLDivElement
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container)
  root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
const firstPage = Array.from({ length: 100 }, (_, index) => ({ ...artifact, id: `33333333-3333-4333-8333-${String(index).padStart(12, '0')}` }))
function button(text: string) {
  const found = [...container.querySelectorAll('button')].find((item) => item.textContent === text)
  if (!found) throw new Error(`Missing button: ${text}`)
  return found
}
async function click(text: string) { await act(async () => button(text).click()) }
const selected = [firstPage[0]]
const onSelected = vi.fn(), onReady = vi.fn()
function panel(kind: 'task' | 'conversation', id = taskId): ReactNode {
  return kind === 'task' ? <TaskArtifacts agentId={agentId} taskId={id} />
    : <ConversationArtifacts agentId={agentId} conversationId={id === taskId ? conversationId : id} selected={selected} onSelected={onSelected} onReady={onReady} blocked={false} />
}
async function render(node: ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const instance = createInstance()
  await instance.init({ lng: language, resources: { [language]: { artifacts: artifactResources[language] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={instance}>{node}</I18nextProvider>))
}
describe.each(['task', 'conversation'] as const)('%s file page recovery', (kind) => {
  it.each(['pt-BR', 'en', 'es'] as const)('retries only the failed offset, preserves cards and continues in %s', async (language) => {
    const text = artifactResources[language]
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(page(firstPage, true, 100)))
      .mockRejectedValueOnce(new Error('offline')).mockRejectedValueOnce(new Error('offline again'))
      .mockResolvedValueOnce(Response.json(page([artifact])))
    vi.stubGlobal('fetch', fetcher)
    await render(panel(kind), language)
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(100)
    await click(text.more)
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(100)
    expect(button(text.more).disabled).toBe(true)
    expect(fetcher).toHaveBeenCalledTimes(2)
    await click(text.retryRead)
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(100)
    expect(fetcher).toHaveBeenCalledTimes(3)
    await click(text.retryRead)
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(101)
    expect(container.textContent).not.toContain(text.readError)
    expect(container.textContent).not.toContain(text.retryRead)
    expect(container.textContent).not.toContain(text.more)
    expect(fetcher.mock.calls.map(([url]) => new URL(url, 'https://bees.test').searchParams.get('offset'))).toEqual(['0', '100', '100', '100'])
    expect(fetcher.mock.calls.every(([, options]) => !options.method || options.method === 'GET')).toBe(true)
    if (kind === 'conversation') expect(container.querySelector('.artifact-selected')?.textContent).toContain(selected[0].name)
  })
  it('recovers an initial failure and synchronously blocks duplicate retry clicks', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('offline'))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve }))
    vi.stubGlobal('fetch', fetcher)
    await render(panel(kind))
    const retry = button(artifactResources['pt-BR'].retryRead)
    await act(async () => { retry.click(); retry.click() })
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(retry.disabled).toBe(true)
    await act(async () => finish(Response.json(page([artifact]))))
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(1)
    expect(fetcher.mock.calls.every(([url]) => url.includes('offset=0&limit=100'))).toBe(true)
  })
  it('aborts a retry and ignores its late response after changing context', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn().mockRejectedValueOnce(new Error('offline'))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve }))
      .mockResolvedValueOnce(Response.json(page([])))
    vi.stubGlobal('fetch', fetcher)
    await render(panel(kind)); await click(artifactResources['pt-BR'].retryRead)
    await render(panel(kind, '88888888-8888-4888-8888-888888888888'))
    expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true)
    await act(async () => finish(Response.json(page([artifact]))))
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(0)
    expect(fetcher).toHaveBeenCalledTimes(3)
  })
})
