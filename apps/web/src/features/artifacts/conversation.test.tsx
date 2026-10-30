// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { ReactNode } from 'react'
import { setCsrfToken } from '../../api/client'
import { productResources } from '../../locales/product'
import { taskResources } from '../../locales/tasks'
import { approvalResources } from '../../locales/approvals'
import { artifactResources } from '../../locales/artifacts'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { useResource } from '../../hooks/useResource'
import { ConversationArtifacts } from './ConversationArtifacts'
import type { ArtifactRecord } from './api'
import { agentId, artifact, conversationId, page, taskId } from './fixtures'
import { prepareBytes } from './mutations'
import { TaskArtifacts } from './TaskArtifacts'

vi.mock('../tasks/useTaskPolling', () => ({ useTaskPolling: vi.fn() }))
vi.mock('../../hooks/useResource', () => ({ useResource: vi.fn() }))
vi.mock('../approvals/ApprovalPanel', () => ({ ApprovalPanel: () => null }))
let root: Root
let container: HTMLDivElement
let wrapper: (node: ReactNode) => ReactNode
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto)
  sessionStorage.clear(); setCsrfToken('test-csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  const instance = createInstance(); await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { product: productResources['pt-BR'], tasks: taskResources['pt-BR'], approvals: approvalResources['pt-BR'], artifacts: artifactResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  wrapper = (node) => <I18nextProvider i18n={instance}>{node}</I18nextProvider>
  vi.mocked(useTaskPolling).mockReturnValue({ data: { delegations: [], has_more: false, next_offset: null }, loading: false, error: null, refresh: vi.fn() })
  vi.mocked(useResource).mockReturnValue({ resource: { state: 'ready', data: { conversation_id: conversationId, messages: [] } }, reload: vi.fn() })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })
async function render(node: ReactNode) { await act(async () => root.render(wrapper(node))) }
function button(text: string) { const element = [...container.querySelectorAll('button')].find((item) => item.textContent === text); if (!element) throw new Error(`Missing button ${text}`); return element }
async function click(element: HTMLElement) { await act(async () => element.click()) }
async function settle(predicate: () => boolean) { for (let i = 0; i < 50 && !predicate(); i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2)) }); expect(predicate()).toBe(true) }
async function chooseFile(content = 'abc', name = 'notes.txt') {
  const input = container.querySelector('input[type=file]') as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: [new File([content], name, { type: 'text/plain' })] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
  await settle(() => container.textContent!.includes('Seleção pronta'))
}
function canonical(id: string, previous: ArtifactRecord | null = null) {
  return { ...artifact, id, source: previous ? 'user_revision' as const : 'user_upload' as const, name: 'notes.txt', task_id: previous?.task_id ?? null, run_id: null, series_id: previous?.series_id ?? previous?.id ?? id, previous_id: previous?.id ?? null, version: previous ? previous.version + 1 : 1 }
}
const filesPanel = (onSelected = vi.fn(), onReady = vi.fn()) => <ConversationArtifacts agentId={agentId} conversationId={conversationId} selected={[]} onSelected={onSelected} onReady={onReady} blocked={false} />

describe('conversation attachment flow with real React state', () => {
  it('stores only after explicit confirmation and never sends to the model by attaching', async () => {
    let files: ArtifactRecord[] = []
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'PUT') { const id = url.split('/').at(-2)!; const published = canonical(id); files = [published]; return Response.json(published) }
      return Response.json(page(files))
    }); vi.stubGlobal('fetch', fetcher)
    await render(filesPanel()); await settle(() => button('Anexar arquivo').disabled === false)
    await click(button('Anexar arquivo')); await chooseFile()
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(0)
    expect(container.textContent).toContain('não enviado ao modelo')
    await click(button('Confirmar armazenamento')); await settle(() => container.textContent!.includes('Publicação confirmada'))
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1)
    expect(fetcher.mock.calls.every(([url]) => !url.endsWith('/chat'))).toBe(true)
    expect(container.textContent).toContain('Arquivo enviado por uma pessoa')
  })
  it('checks a lost publication by UUID without retrying and fences the cancelled late response', async () => {
    let finish!: (response: Response) => void; let published: ArtifactRecord | null = null
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'PUT') { published = canonical(url.split('/').at(-2)!); return new Promise<Response>((resolve) => { finish = resolve }) }
      if (!url.includes('?')) return Response.json(published)
      return Response.json(page(published ? [published] : []))
    }); vi.stubGlobal('fetch', fetcher)
    await render(filesPanel()); await settle(() => !button('Anexar arquivo').disabled)
    await click(button('Anexar arquivo')); await chooseFile(); await click(button('Confirmar armazenamento'))
    await click(button('Interromper transporte')); await act(async () => finish(Response.json(published)))
    expect(container.textContent).toContain('Publicação não confirmada'); expect(container.textContent).not.toContain('Publicação confirmada.')
    await click(button('Consultar publicação')); await settle(() => container.textContent!.includes('Publicação confirmada.'))
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1)
  })
  it('preserves a conflicting candidate and requires choosing the current version before new confirmation', async () => {
    const next = { ...artifact, id: '55555555-5555-4555-8555-555555555555', run_id: null, version: 2, source: 'user_revision' as const, previous_id: artifact.id }
    let files = [artifact]; let attempts = 0
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'PUT') { attempts++; if (attempts === 1) { files = [next, artifact]; return Response.json({ error: { code: 'artifact_version_conflict' } }, { status: 409 }) }; return Response.json(canonical(url.split('/').at(-2)!, next)) }
      return Response.json(page(files))
    }); vi.stubGlobal('fetch', fetcher)
    await render(filesPanel()); await settle(() => container.querySelectorAll('.artifact-card').length === 1)
    await click(button('Revisar arquivo')); await chooseFile(); await click(button('Confirmar nova versão'))
    await settle(() => container.textContent!.includes('Seu candidato foi conservado'))
    expect(attempts).toBe(1)
    await click(button('Atualizar arquivos')); await settle(() => container.querySelectorAll('.artifact-card').length === 2)
    await click(button('Revisar arquivo')); expect(container.textContent).toContain('notes.txt'); expect(container.textContent).toContain('versão 2')
    expect(attempts).toBe(1); await click(button('Confirmar nova versão')); await settle(() => container.textContent!.includes('Publicação confirmada.'))
    const puts = fetcher.mock.calls.filter(([, options]) => options.method === 'PUT'); expect(puts[0][0]).not.toBe(puts[1][0])
    const metadata = (puts[1][1].headers as Record<string, string>)['X-Bees-Artifact']; expect(JSON.parse(atob(metadata.replaceAll('-', '+').replaceAll('_', '/'))).previous_id).toBe(next.id)
  })
  it('late list from another conversation never leaks cards or enables stale controls', async () => {
    let finish!: (response: Response) => void
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve })).mockResolvedValue(Response.json(page([]))); vi.stubGlobal('fetch', fetcher)
    await render(filesPanel())
    const other = '88888888-8888-4888-8888-888888888888'
    await render(<ConversationArtifacts agentId={agentId} conversationId={other} selected={[]} onSelected={vi.fn()} onReady={vi.fn()} blocked={false} />)
    await act(async () => finish(Response.json(page())))
    expect(container.textContent).not.toContain(artifact.name); expect(container.textContent).toContain('Nenhum arquivo anexado')
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
  })
  it('renders dangerous content only as escaped text and never enables editing a truncated preview', async () => {
    const text = '<script>globalThis.executed=true</script>\n=HYPERLINK("https://example.test")\n<a href="javascript:alert(1)">link</a>'
    const candidate = await prepareBytes('unsafe.md', 'text/markdown', new TextEncoder().encode(text))
    const file = { ...artifact, name: candidate.name, media_type: candidate.media_type, size_bytes: candidate.size_bytes, sha256: candidate.sha256 }
    let large = false
    const fetcher = vi.fn(async (url: string) => url.includes('/preview?') ? Response.json({ artifact: large ? { ...file, size_bytes: 70000 } : file, text: large ? 'a'.repeat(65536) : text, truncated: large, total_bytes: large ? 70000 : candidate.size_bytes }) : Response.json(page([large ? { ...file, size_bytes: 70000 } : file]))); vi.stubGlobal('fetch', fetcher)
    await render(filesPanel()); await settle(() => container.querySelector('.artifact-card') !== null)
    await click(button('Ver texto')); await settle(() => container.querySelector('pre') !== null)
    expect(container.querySelector('pre')?.textContent).toBe(text); expect(container.querySelector('script')).toBeNull(); expect(container.querySelector('a')).toBeNull()
    await click(button('Fechar')); large = true; await click(button('Atualizar arquivos'))
    // A changed immutable size is rejected; use a fresh context reader for the large version.
    await render(<div />); await render(filesPanel()); await settle(() => container.querySelector('.artifact-card') !== null)
    await click(button('Revisar arquivo')); await click(button('Abrir editor de texto')); await settle(() => container.textContent!.includes('O conteúdo excede 64 KiB'))
    expect(container.querySelector('.artifact-text-editor textarea')).toBeNull(); expect(container.querySelector('input[type=file]')).not.toBeNull()
  })
  it('reviews a legacy task output through its own conversation while preserving task linkage', async () => {
    let files = [artifact]
    const fetcher = vi.fn(async (url: string, options: RequestInit) => { if (options.method === 'PUT') { const revised = canonical(url.split('/').at(-2)!, artifact); files = [revised, artifact]; return Response.json(revised) }; return Response.json(page(files)) }); vi.stubGlobal('fetch', fetcher)
    await render(<TaskArtifacts agentId={agentId} taskId={taskId} />); await settle(() => container.querySelector('.artifact-card') !== null)
    await click(button('Revisar arquivo')); await chooseFile(); await click(button('Confirmar nova versão')); await settle(() => container.textContent!.includes('Publicação confirmada.'))
    expect(container.querySelectorAll('.artifact-card')).toHaveLength(2)
    const [url, options] = fetcher.mock.calls.find(([, options]) => options.method === 'PUT')!
    expect(url).toContain(`/conversations/${conversationId}/artifacts/`)
    const metadata = (options.headers as Record<string, string>)['X-Bees-Artifact']; expect(JSON.parse(atob(metadata.replaceAll('-', '+').replaceAll('_', '/')))).toMatchObject({ previous_id: artifact.id, expected_revision: artifact.revision })
    expect(files[0].task_id).toBe(taskId); expect(files[0].run_id).toBeNull(); expect(files[1].run_id).toBe(artifact.run_id)
    expect(fetcher.mock.calls.every(([url]) => !url.endsWith('/chat'))).toBe(true); expect(container.querySelector('.artifact-card input[type=checkbox]')).toBeNull()
  })
  it('blocks another task-card revision while the original publication is uncertain and fences its late success', async () => {
    let finish!: (response: Response) => void; let published: ArtifactRecord | null = null
    const fetcher = vi.fn(async (url: string, options: RequestInit) => { if (options.method === 'PUT') { published = canonical(url.split('/').at(-2)!, artifact); return new Promise<Response>((resolve) => { finish = resolve }) }; return Response.json(page()) }); vi.stubGlobal('fetch', fetcher)
    await render(<TaskArtifacts agentId={agentId} taskId={taskId} />); await settle(() => container.querySelector('.artifact-card') !== null)
    await click(button('Revisar arquivo')); await chooseFile(); await click(button('Confirmar nova versão')); expect(button('Revisar arquivo').disabled).toBe(true)
    await click(button('Interromper transporte')); expect(button('Revisar arquivo').disabled).toBe(true)
    await act(async () => finish(Response.json(published)))
    expect(container.textContent).toContain('Publicação não confirmada'); expect(container.textContent).not.toContain('Publicação confirmada.')
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1)
  })
  it('keeps downloads for a legacy task with no conversation without inventing a revision destination', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(page([{ ...artifact, conversation_id: null }]))))
    await render(<TaskArtifacts agentId={agentId} taskId={taskId} />); await settle(() => container.querySelector('.artifact-card') !== null)
    expect(container.textContent).toContain('Sem conversa vinculada'); expect(container.textContent).toContain('Baixar versão 1'); expect(container.textContent).not.toContain('Revisar arquivo'); expect(container.textContent).not.toContain('Ver texto')
  })
})
