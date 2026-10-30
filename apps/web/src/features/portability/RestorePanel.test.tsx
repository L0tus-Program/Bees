// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { restorationResources } from '../../locales/restoration'
import { restoreReviews } from './restoration'
import { RESTORE_PENDING_KEY } from './restoreSession'
import RestorePanel from './RestorePanel'

const report = { restore_id: '10000000-0000-4000-8000-000000000001', source_id: '20000000-0000-4000-8000-000000000001', destination: 'recuperada', status: 'inactive', created_at: '2026-10-21T12:00:00Z', package_sha256: 'a'.repeat(64), source_schema: 18, destination_schema: 18, records: { agents: 2, messages: 3 }, blobs: 1, changes: [], review_required: [...restoreReviews], source_evidence: 'source.zip', revision_policy: 'new-installation-baseline-original-revisions' }
const aliases = [{ id: 'recuperada', available: true, state: 'inactive_only' }, { id: 'segunda', available: true, state: 'inactive_only' }]
const pt = restorationResources['pt-BR']
let root: Root, container: HTMLDivElement
const onBusy = vi.fn(), onClose = vi.fn()
beforeEach(() => { vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); setCsrfToken('csrf'); sessionStorage.clear(); container = document.createElement('div'); document.body.append(container); root = createRoot(container); vi.clearAllMocks() })
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); vi.restoreAllMocks(); setCsrfToken() })
async function render(lang: keyof typeof restorationResources = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: lang, fallbackLng: lang, resources: { [lang]: { restoration: restorationResources[lang] } }, interpolation: { escapeValue: false } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><RestorePanel onClose={onClose} onBusy={onBusy} /></I18nextProvider>))
}
const button = (text: string) => Array.from(container.querySelectorAll('button')).find(item => item.textContent === text)!
async function click(text: string) { await act(async () => button(text).click()) }
async function choose(value = 'recuperada') { await act(async () => { const select = container.querySelector('select')!; select.value = value; select.dispatchEvent(new Event('change', { bubbles: true })) }) }
async function file() { await act(async () => { const input = container.querySelector('input[type=file]')!; Object.defineProperty(input, 'files', { configurable: true, value: [new File(['private'], 'private.zip')] }); input.dispatchEvent(new Event('change', { bubbles: true })) }) }
async function consent() { await act(async () => (container.querySelector('input[type=checkbox]') as HTMLInputElement).click()) }
function setupFetch() {
  const fetcher = vi.fn().mockImplementation((url: string) => Promise.resolve(url.endsWith('/destinations') ? Response.json(aliases) : url.endsWith('/report') ? Response.json({ error: { code: 'restoration_not_found' } }, { status: 404 }) : Response.json(report)))
  vi.stubGlobal('fetch', fetcher); return fetcher
}
async function prepare() { await render(); await click(pt.refresh); await choose(); await click(pt.consult); await file(); await consent() }
for (const lang of ['pt-BR', 'en', 'es'] as const) it(`opens in ${lang} without HTTP/upload and with fresh consent`, async () => {
  const fetcher = setupFetch(); await render(lang)
  expect(fetcher).not.toHaveBeenCalled(); expect(container.textContent).toContain(restorationResources[lang].scope)
  expect(document.activeElement).toBe(container.querySelector('h1')); expect((container.querySelector('input[type=checkbox]') as HTMLInputElement).checked).toBe(false); expect(button(restorationResources[lang].send).disabled).toBe(true)
})
it('requires report lookup, explicit file and consent, then publishes once and shows all human review requirements', async () => {
  const fetcher = setupFetch(); await render(); await click(pt.refresh); await choose(); expect(container.querySelector('fieldset')!.disabled).toBe(true)
  await click(pt.consult); expect(container.querySelector('fieldset')!.disabled).toBe(false); await file(); expect(button(pt.send).disabled).toBe(true); await consent(); await click(pt.send)
  expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1); expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBeNull(); expect(container.textContent).toContain(pt.confirmedHelp)
  for (const key of restoreReviews) expect(container.textContent).toContain(pt[key]); expect(container.querySelector('form')).toBeNull(); expect(container.querySelector('option[value=recuperada]')!.textContent).toContain(pt.occupied)
})
it('keeps a lost upload locked to the same alias across reopen, recovers by GET only', async () => {
  const fetcher = setupFetch(); await prepare(); fetcher.mockRejectedValueOnce(new Error('lost')); await click(pt.send)
  expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBe('recuperada'); expect((container.querySelector('select') as HTMLSelectElement).disabled).toBe(true)
  await act(async () => root.unmount()); root = createRoot(container); await render(); expect(container.textContent).toContain('recuperada'); expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1)
  fetcher.mockResolvedValueOnce(Response.json(report)); await click(pt.consult); expect(container.textContent).toContain(pt.confirmed); expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBeNull(); expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1)
})
it('does not treat missing report after lost upload as proof of failure or permit another upload', async () => {
  sessionStorage.setItem(RESTORE_PENDING_KEY, 'recuperada'); setupFetch(); await render(); await click(pt.consult)
  expect(container.textContent).toContain(pt.noReport); expect(container.querySelector('fieldset')!.disabled).toBe(true); expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBe('recuperada')
})
it('blocks upload before HTTP on denied or silently lost tab storage', async () => {
  for (const fail of ['throw', 'silent']) {
    const fetcher = setupFetch(); await prepare(); vi.stubGlobal('sessionStorage', { getItem: () => null, setItem: () => { if (fail === 'throw') throw Error('denied') } }); await click(pt.send)
    expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(0); expect(container.textContent).toContain(pt.storage)
    vi.unstubAllGlobals(); vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); await act(async () => root.unmount()); root = createRoot(container)
  }
})
it('requires fresh report/file/consent after changing destination', async () => {
  setupFetch(); await prepare(); await choose('segunda'); expect(container.querySelector('fieldset')!.disabled).toBe(true); expect((container.querySelector('input[type=checkbox]') as HTMLInputElement).checked).toBe(false); expect(button(pt.send).disabled).toBe(true)
})
it('shows configuration prerequisite without inventing a destination', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'restoration_not_configured' } }, { status: 503 }))); await render(); await click(pt.refresh); expect(container.textContent).toContain(pt.notConfigured); expect(button(pt.send).disabled).toBe(true)
})
it('permits a new explicit attempt only after a definite invalid-package refusal and a fresh report check', async () => {
  const fetcher = setupFetch(); await prepare(); fetcher.mockResolvedValueOnce(Response.json({ error: { code: 'portability_invalid_package' } }, { status: 409 })); await click(pt.send)
  expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBeNull(); expect(container.querySelector('fieldset')!.disabled).toBe(true); expect((container.querySelector('input[type=checkbox]') as HTMLInputElement).checked).toBe(false)
  await click(pt.consult); await consent(); await click(pt.send); expect(container.textContent).toContain(pt.confirmed); expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(2)
})
it('ignores late confirmation after unmount and never duplicates a rapid submit', async () => {
  const fetcher = setupFetch(); await prepare(); let resolve!: (value: Response) => void; fetcher.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await act(async () => { button(pt.send).click(); button(pt.send).click() }); expect(fetcher.mock.calls.filter(call => call[1].method === 'POST')).toHaveLength(1)
  const signal = fetcher.mock.calls.at(-1)![1].signal; await act(async () => root.unmount()); root = createRoot(container); expect(signal.aborted).toBe(true)
  await act(async () => resolve(Response.json(report))); expect(sessionStorage.getItem(RESTORE_PENDING_KEY)).toBe('recuperada'); expect(onBusy.mock.calls.filter(call => call[0] === false)).toHaveLength(2)
})
it('does not enable upload into an occupied alias without a report', async () => {
  const fetcher = setupFetch(); fetcher.mockResolvedValueOnce(Response.json([{ ...aliases[0], available: false }])); await render(); await click(pt.refresh); await choose(); await click(pt.consult); expect(container.querySelector('fieldset')!.disabled).toBe(true)
})
it('rejects invalid or unavailable stored aliases without uploading', async () => {
  sessionStorage.setItem(RESTORE_PENDING_KEY, '../private/path'); const fetcher = setupFetch(); await render(); expect(container.textContent).toContain(pt.storage); expect(fetcher).not.toHaveBeenCalled(); expect(button(pt.send).disabled).toBe(true)
})
