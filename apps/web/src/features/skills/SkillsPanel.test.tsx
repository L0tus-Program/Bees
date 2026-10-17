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
import { agent as chatAgent } from '../chat/testFixtures'
import { skillResources } from '../../locales/skills'
import { productResources } from '../../locales/product'
import { SkillDescription, SkillsPanel } from './SkillsPanel'
import { agentId, definition, skill } from './fixtures'

let root: Root
let container: HTMLDivElement
let wrapper: (node: ReactNode) => ReactNode
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  const i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: { 'pt-BR': { skills: skillResources['pt-BR'], product: productResources['pt-BR'] } }, interpolation: { escapeValue: false } })
  wrapper = (node) => <I18nextProvider i18n={i18n}>{node}</I18nextProvider>
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })
async function render(node: ReactNode = <SkillsPanel agent={{ ...chatAgent, id: agentId }} />) { await act(async () => root.render(wrapper(node))) }
function button(text: string) { const value = [...container.querySelectorAll('button')].find((item) => item.textContent === text); if (!value) throw new Error('Missing ' + text); return value }
async function click(value: HTMLElement) { await act(async () => value.click()) }
async function chooseFile(value: unknown) {
  const input = container.querySelector('input[type=file]') as HTMLInputElement
  Object.defineProperty(input, 'files', { configurable: true, value: [new File([JSON.stringify(value)], 'procedure.json', { type: 'application/json' })] })
  await act(async () => input.dispatchEvent(new Event('change', { bubbles: true })))
}

describe('human skill library', () => {
  it('reviews the exact version before enabling and never dispatches a task', async () => {
    let current = skill
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'PUT') { current = { ...skill, revision: 2, enabled_version: 1 }; return Response.json(current) }
      return Response.json([current])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Revisar e habilitar'))
    expect(container.textContent).toContain('Revisar versão 1'); expect(container.textContent).toContain('Registrar fonte e data')
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(0)
    await click(button('Habilitar esta versão'))
    const sent = fetcher.mock.calls.find(([, options]) => options.method === 'PUT')!
    expect(JSON.parse(sent[1].body as string)).toMatchObject({ expected_revision: 1, version: 1, enabled: true })
    expect(container.textContent).toContain('Habilitada')
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/chat'))).toBe(true)
  })
  it('imports only after review and resolves lost writes with the original UUID', async () => {
    let attempts = 0
    const imported = { ...skill, current: { ...skill.current, origin: 'import' } }
    const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') { attempts++; if (attempts === 1) throw new TypeError('Lost'); return Response.json(imported) }
      return Response.json(attempts > 1 ? [imported] : [])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await chooseFile({ format: 'bees.skill/v1', scope: 'agent', definition })
    expect(container.textContent).toContain('Revisar cópia importada')
    expect(attempts).toBe(0)
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(attempts).toBe(1); expect(button('Novo procedimento').disabled).toBe(true)
    await click(button('Verificar o mesmo pedido'))
    expect(attempts).toBe(2)
    const posts = fetcher.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(posts[0][1].body).toBe(posts[1][1].body)
    expect(JSON.parse(posts[1][1].body as string)).not.toHaveProperty('enabled')
    expect(container.textContent).toContain('Versão salva')
  })
  it('updates the candidate when another import is selected and rejects executable fields', async () => {
    const fetcher = vi.fn().mockImplementation(async () => Response.json([])); vi.stubGlobal('fetch', fetcher)
    await render()
    const document = { format: 'bees.skill/v1', scope: 'agent', definition }
    await chooseFile(document)
    expect((container.querySelector('.skill-editor input') as HTMLInputElement).value).toBe(definition.name)
    await chooseFile({ ...document, definition: { ...definition, name: 'Segunda seleção' } })
    expect((container.querySelector('.skill-editor input') as HTMLInputElement).value).toBe('Segunda seleção')
    await chooseFile({ ...document, script: 'exec()' })
    expect(container.querySelector('[role=alert]')).not.toBeNull()
    expect(fetcher.mock.calls).toHaveLength(1)
  })
  it('renders procedures as escaped text and preserves declared resources', async () => {
    await render(<SkillDescription definition={{ ...definition, purpose: '<script>execute()</script>' }} />)
    expect(container.querySelector('script')).toBeNull()
    expect(container.textContent).toContain('<script>execute()</script>')
    expect(container.textContent).toContain('browser.read')
    expect(container.textContent).toContain('Computador da abelha')
  })
  it('does not replace the latest imported candidate with a late file read', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json([])))
    let finish!: (value: string) => void
    const document = { format: 'bees.skill/v1', scope: 'agent', definition }
    const second = { ...document, definition: { ...definition, name: 'Seleção mais recente' } }
    const read = vi.spyOn(File.prototype, 'text').mockImplementationOnce(() => new Promise((resolve) => { finish = resolve })).mockResolvedValueOnce(JSON.stringify(second))
    try {
      await render(); await chooseFile(document); await chooseFile(second)
      await act(async () => finish(JSON.stringify(document)))
      expect((container.querySelector('.skill-editor input') as HTMLInputElement).value).toBe('Seleção mais recente')
    } finally { read.mockRestore() }
  })
})
