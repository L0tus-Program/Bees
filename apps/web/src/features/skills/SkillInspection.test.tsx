// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../../api/client'
import { SkillsPanel } from './SkillsPanel'
import { agentId, definition, skill } from './fixtures'
import { agent as chatAgent } from '../chat/testFixtures'
import { skillResources } from '../../locales/skills'
import { productResources } from '../../locales/product'

let root: Root
let container: HTMLDivElement
let language: keyof typeof skillResources
function pending() { let resolve!: (value: Response) => void; const promise = new Promise<Response>((yes) => { resolve = yes }); return { promise, resolve } }
const exported = { format: 'bees.skill/v1', scope: 'agent', definition }
function button(key: keyof typeof skillResources['pt-BR']) {
  const value = [...container.querySelectorAll('button')].find((item) => item.textContent === skillResources[language][key])
  if (!value) throw new Error(`Missing ${key}`)
  return value
}
async function idle() { await vi.waitFor(async () => { await act(async () => {}); expect(container.querySelector('[aria-busy=true]')).toBeNull() }) }
async function click(key: keyof typeof skillResources['pt-BR']) { await act(async () => button(key).click()) }
async function mount(id = agentId) {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { skills: skillResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><SkillsPanel agent={{ ...chatAgent, id }} /></I18nextProvider>)); await idle()
}
beforeEach(() => {
  sessionStorage.clear(); setCsrfToken('csrf'); vi.stubGlobal('crypto', webcrypto); vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); setCsrfToken() })

describe.each(['pt-BR', 'en', 'es'] as const)('skill inspections in %s', (lng) => {
  beforeEach(() => { language = lng })
  it('serializes same-turn history and export clicks and unlocks after success', async () => {
    const response = pending()
    const fetcher = vi.fn(async (url: string) => url.includes('/revisions?') ? response.promise : Response.json([skill]))
    vi.stubGlobal('fetch', fetcher); await mount()
    const history = button('history'), exporting = button('export')
    await act(async () => { history.click(); history.click(); exporting.click() })
    expect(fetcher).toHaveBeenCalledTimes(2)
    await act(async () => response.resolve(Response.json([skill.current]))); await idle()
    expect(container.querySelector('.skill-history')).not.toBeNull()
    expect(button('refresh').disabled).toBe(false)
  })
  it.each([true, false])('serializes inspection and mutation in the same turn, inspection first: %s', async (inspectionFirst) => {
    const response = pending()
    const enabled = { ...skill, enabled_version: 1 }
    const fetcher = vi.fn(async (url: string, options: RequestInit) => options.method === 'PUT' || url.includes('/revisions?') ? response.promise : Response.json([enabled]))
    vi.stubGlobal('fetch', fetcher); await mount()
    const history = button('history'), disable = button('disable')
    await act(async () => { if (inspectionFirst) { history.click(); disable.click() } else { disable.click(); history.click() } })
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(fetcher.mock.calls[1][1].method).toBe(inspectionFirst ? 'GET' : 'PUT')
    await act(async () => response.resolve(Response.json(inspectionFirst ? [skill.current] : { ...skill, revision: 2 }))); await idle()
    expect(button('refresh').disabled).toBe(false)
    expect(sessionStorage.length).toBe(0)
  })
  it('keeps origin active when refresh is clicked in the same turn and releases busy', async () => {
    const response = pending()
    const fetcher = vi.fn(async (url: string) => url.endsWith('/process') ? response.promise : Response.json(url.includes('/revisions?') ? [skill.current] : [skill]))
    vi.stubGlobal('fetch', fetcher); await mount(); await click('history'); await idle()
    const origin = button('processOrigin'), refresh = button('refresh')
    await act(async () => { origin.click(); refresh.click() })
    expect(fetcher).toHaveBeenCalledTimes(3)
    await act(async () => response.resolve(Response.json(null))); await idle()
    expect(container.textContent).toContain(skillResources[lng].processNoOrigin)
    expect(button('refresh').disabled).toBe(false)
    await click('refresh'); await idle(); expect(fetcher).toHaveBeenCalledTimes(4)
  })
  it.each(['history', 'export', 'processOrigin'] as const)('aborts %s and ignores late completion after switching agent', async (operation) => {
    const response = pending(); let signal: AbortSignal | undefined
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
    const anchor = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      const matches = operation === 'history' ? url.includes('/revisions?') : operation === 'export' ? url.endsWith('/export') : url.endsWith('/process')
      if (matches) { signal = options.signal as AbortSignal; return response.promise }
      return Response.json(url.includes('/revisions?') ? [skill.current] : url.includes(agentId) ? [skill] : [])
    })
    vi.stubGlobal('fetch', fetcher); await mount()
    if (operation === 'processOrigin') { await click('history'); await idle() }
    await click(operation); expect(signal?.aborted).toBe(false)
    await mount('33333333-3333-4333-8333-333333333333'); expect(signal?.aborted).toBe(true)
    await act(async () => response.resolve(Response.json(operation === 'history' ? [skill.current] : operation === 'export' ? exported : null)))
    expect(create).not.toHaveBeenCalled(); expect(anchor).not.toHaveBeenCalled()
    expect(container.querySelector('.skill-history')).toBeNull(); expect(container.querySelector('[role=alert]')).toBeNull()
    expect(button('refresh').disabled).toBe(false)
  })
  it.each(['history', 'export', 'processOrigin'] as const)('releases controls after %s fails and clears error on a new inspection', async (operation) => {
    let fail = true
    const fetcher = vi.fn(async (url: string) => {
      const matches = operation === 'history' ? url.includes('/revisions?') : operation === 'export' ? url.endsWith('/export') : url.endsWith('/process')
      if (matches && fail) throw new TypeError('offline')
      return Response.json(url.endsWith('/export') ? exported : url.endsWith('/process') ? null : url.includes('/revisions?') ? [skill.current] : [skill])
    })
    vi.stubGlobal('fetch', fetcher); vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test'); vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {}); vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await mount(); if (operation === 'processOrigin') { await click('history'); await idle() }
    await click(operation); await idle()
    expect(container.querySelector('[role=alert]')).not.toBeNull(); expect(button('refresh').disabled).toBe(false)
    fail = false; await click(operation); await idle(); expect(container.querySelector('[role=alert]')).toBeNull()
  })
  it('downloads exactly once for duplicate clicks and forwards cancellation', async () => {
    const response = pending(); let signal: AbortSignal | undefined
    const fetcher = vi.fn(async (url: string, options: RequestInit) => { if (url.endsWith('/export')) { signal = options.signal as AbortSignal; return response.promise } return Response.json([skill]) })
    vi.stubGlobal('fetch', fetcher)
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test'); vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const anchor = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await mount(); const exporting = button('export'); await act(async () => { exporting.click(); exporting.click() })
    expect(fetcher).toHaveBeenCalledTimes(2); expect(signal).toBeDefined()
    await act(async () => response.resolve(Response.json(exported))); await idle()
    expect(create).toHaveBeenCalledTimes(1); expect(anchor).toHaveBeenCalledTimes(1); expect(button('refresh').disabled).toBe(false)
  })
})
