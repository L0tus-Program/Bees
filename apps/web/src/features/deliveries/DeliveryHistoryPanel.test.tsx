// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { getDeliveries, getDelivery } from '../../api/deliveryHistory'
import type { DeliveryContent } from '../../api/deliveryHistory'
import { deliveryResources } from '../../locales/deliveries'
import { artifactResources } from '../../locales/artifacts'
import { agent as fixtureAgent } from '../chat/testFixtures'
import { DeliveryHistoryPanel } from './DeliveryHistoryPanel'
import { agentId, artifact, page, result } from './fixtures'
vi.mock('../../api/deliveryHistory', async (original) => ({ ...await original<typeof import('../../api/deliveryHistory')>(), getDeliveries: vi.fn(), getDelivery: vi.fn() }))
vi.mock('../tasks/TaskDetail', () => ({ TaskDetail: ({ taskId, onClose }: { taskId: string; onClose: () => void }) => <div data-task={taskId}><button type="button" onClick={onClose}>Close task fixture</button></div> }))
const agent = { ...fixtureAgent, id: agentId }
let root: Root
let container: HTMLDivElement
let wrapper: (node: ReactNode) => ReactNode
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  const instance = createInstance(); await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { deliveries: deliveryResources['pt-BR'], artifacts: artifactResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  wrapper = (node) => <I18nextProvider i18n={instance}>{node}</I18nextProvider>
  vi.mocked(getDeliveries).mockImplementation(async (id, scope) => ({ ...page(), agent_id: id, scope, items: [{ ...result, agent_id: id }] }))
  vi.mocked(getDelivery).mockResolvedValue({ kind: 'result', item: result, content: 'Texto integral preservado <script>inofensivo</script>' })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks() })
async function render(node: ReactNode) { await act(async () => root.render(wrapper(node))) }
function button(text: string) { const found = [...container.querySelectorAll('button')].find((node) => node.textContent === text); if (!found) throw new Error(`Missing button: ${text}`); return found }
async function click(node: HTMLElement) { await act(async () => node.click()) }
async function settle(predicate: () => boolean) { for (let i = 0; i < 50 && !predicate(); i++) await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2)) }); expect(predicate()).toBe(true) }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done }); return { promise, resolve } }
async function text(value: string) {
  const input = container.querySelector('input')!
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })) })
}
describe('delivery history with actual React state and native controls', () => {
  it('opens exact preserved content, focuses the heading and restores focus on close without mutations', async () => {
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />)
    await click(button('Abrir entrega')); await settle(() => container.textContent!.includes('Texto integral preservado'))
    expect(document.activeElement?.textContent).toContain('Entrega selecionada')
    expect(container.querySelector('script')).toBeNull(); expect(container.textContent).toContain('<script>inofensivo</script>')
    expect(vi.mocked(getDelivery).mock.calls[0].slice(0, 2)).toEqual([agentId, result])
    const reference = container.querySelector('textarea')!
    expect(reference.readOnly).toBe(true); expect(reference.value).toContain(`task=${result.task_id}`); expect(reference.value).toContain('version=1')
    await click(button('Abrir tarefa e decisões')); expect(container.querySelector('[data-task]')?.getAttribute('data-task')).toBe(result.task_id)
    await click(button('Close task fixture')); expect(document.activeElement?.textContent).toBe('Abrir tarefa e decisões')
    await click(button('Fechar entrega')); expect(document.activeElement?.textContent).toBe('Abrir entrega')
  })
  it('has discoverable skill and routine buttons with explicit unavailable behavior', async () => {
    const onSkills = vi.fn(); await render(<DeliveryHistoryPanel agent={agent} onSkills={onSkills} />)
    await click(button('Abrir biblioteca de skills')); expect(onSkills).toHaveBeenCalledTimes(1)
    await click(button('Rotinas')); expect(container.textContent).toContain('Nenhuma rotina foi criada')
    expect(getDelivery).not.toHaveBeenCalled()
  })
  it('preserves typed draft text until submit and replaces the submitted query without stale results', async () => {
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />)
    const pending = deferred<ReturnType<typeof page>>()
    vi.mocked(getDeliveries).mockReturnValueOnce(pending.promise).mockImplementation(async (id, scope) => ({ ...page([]), agent_id: id, scope }))
    await text('old'); await click(button('Buscar entregas'))
    expect(vi.mocked(getDeliveries).mock.calls.at(-1)?.[1].query).toBe('old')
    await text('new'); await click(button('Buscar entregas'))
    await act(async () => pending.resolve(page([{ ...result, title: 'Resposta atrasada' }])))
    expect(container.textContent).toContain('Nenhuma entrega encontrada'); expect(container.textContent).not.toContain('Resposta atrasada')
    expect((container.querySelector('input') as HTMLInputElement).value).toBe('new')
  })
  it('aborts pending query transport and ignores a late result after switching bee', async () => {
    const pending = deferred<ReturnType<typeof page>>(); vi.mocked(getDeliveries).mockReturnValueOnce(pending.promise)
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />)
    await click(button('Interromper consulta')); expect(container.textContent).toContain('Consulta interrompida')
    expect(vi.mocked(getDeliveries).mock.calls[0][3]?.aborted).toBe(true)
    const other = { ...agent, id: '99999999-9999-4999-8999-999999999999' }
    await render(<DeliveryHistoryPanel agent={other} onSkills={vi.fn()} />)
    await act(async () => pending.resolve(page([{ ...result, title: 'Abelha anterior' }])))
    expect(container.textContent).not.toContain('Abelha anterior'); expect(container.textContent).toContain('Resultado antigo')
  })
  it('ignores an old delivery response after switching bee and removes the old selection', async () => {
    const pending = deferred<DeliveryContent>(); vi.mocked(getDelivery).mockReturnValueOnce(pending.promise)
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />); await click(button('Abrir entrega'))
    await render(<DeliveryHistoryPanel agent={{ ...agent, id: '99999999-9999-4999-8999-999999999999' }} onSkills={vi.fn()} />)
    await act(async () => pending.resolve({ kind: 'result', item: result, content: 'Conteúdo privado anterior' }))
    expect(container.textContent).not.toContain('Conteúdo privado anterior'); expect(container.querySelector('textarea')).toBeNull()
    expect(vi.mocked(getDelivery).mock.calls[0][2]?.aborted).toBe(true)
  })
  it('keeps deleted versions identifiable with no download and labels partial delivery', async () => {
    vi.mocked(getDeliveries).mockResolvedValue(page([{ ...result, kind: 'artifact', id: artifact.id, revision: artifact.revision, status: 'ready' }]))
    vi.mocked(getDelivery).mockResolvedValue({ kind: 'artifact', artifact: { ...artifact, status: 'deleted', revision: artifact.revision + 1 } })
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />)
    expect(container.textContent).toContain('Entrega parcial'); await click(button('Abrir entrega'))
    expect(container.textContent).toContain('Removido'); expect(container.textContent).toContain(artifact.id)
    expect([...container.querySelectorAll('button')].some((node) => node.textContent?.includes('Baixar'))).toBe(false)
  })
  it('offers a keyboard-selectable reference when clipboard access fails', async () => {
    vi.stubGlobal('navigator', { clipboard: { writeText: vi.fn(async () => { throw new Error('unavailable') }) } })
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />); await click(button('Abrir entrega'))
    await click(button('Copiar referência'))
    expect(container.textContent).toContain('Não foi possível copiar automaticamente')
    const reference = container.querySelector('textarea')!
    expect(document.activeElement).toBe(reference); expect(reference.selectionEnd).toBe(reference.value.length)
  })
  it('reports a detail failure instead of showing an invented empty or replay action', async () => {
    vi.mocked(getDelivery).mockRejectedValue(new Error('gone'))
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />); await click(button('Abrir entrega'))
    expect(container.querySelector('[role=alert]')?.textContent).toContain('Não foi possível consultar')
    expect(container.querySelector('pre')).toBeNull()
  })
  it('focuses the page summary after loading the final older page', async () => {
    vi.mocked(getDeliveries).mockResolvedValueOnce(page([result], 'next')).mockResolvedValueOnce(page([{ ...result, id: '99999999-9999-4999-8999-999999999999', title: 'Entrega mais antiga' }]))
    await render(<DeliveryHistoryPanel agent={agent} onSkills={vi.fn()} />); await click(button('Carregar entregas mais antigas'))
    expect(container.textContent).toContain('Entrega mais antiga'); expect(document.activeElement?.textContent).toContain('2 entrega(s) exibida(s)')
  })
})
