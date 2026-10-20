// @vitest-environment happy-dom
import { createHash, webcrypto } from 'node:crypto'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import { portabilityResources } from '../../locales/portability'
import ExportPanel from './ExportPanel'
import { EXPORT_STATE_KEY, rememberExportPending, rememberExportReceipt, restoreExportState } from './session'

const bytes = new TextEncoder().encode('PRIVATE_PACKAGE_CONTENT')
const receipt = { id: '10000000-0000-4000-8000-000000000001', sha256: createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length, rows: 12, blobs: 1, status: 'ready' as const }
let root: Root, container: HTMLDivElement
const onBusy = vi.fn(), onClose = vi.fn()
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('test-csrf'); sessionStorage.clear()
  vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:private-test-copy'); vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks(); setCsrfToken() })
async function render(language: keyof typeof portabilityResources = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { portability: portabilityResources[language] } } })
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ExportPanel onClose={onClose} onBusy={onBusy} /></I18nextProvider>))
}
function button(text: string) { const value = [...container.querySelectorAll('button')].find(button => button.textContent === text); if (!value) throw new Error('Missing '+text); return value }
async function click(text: string) { await act(async () => button(text).click()) }
async function downloaded() {
  await vi.waitFor(async () => {
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)) })
    expect(container.querySelector('.export-panel')!.getAttribute('aria-busy')).toBe('false')
  })
}
async function consent() { await act(async () => (container.querySelector('input[type=checkbox]') as HTMLInputElement).click()) }
async function reopen() { await act(async () => root.unmount()); root = createRoot(container); await render() }
function packageResponse(body = bytes) {
  return new Response(body.slice().buffer, { headers: { 'Content-Type': 'application/zip', 'Content-Length': String(bytes.length), 'Content-Disposition': `attachment; filename="bees-export-${receipt.id}.zip"`, 'Cache-Control': 'no-store', Pragma: 'no-cache', 'X-Content-Type-Options': 'nosniff' } })
}
describe('explicit private export workflow', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('explains installation-wide privacy and starts inert with fresh consent in %s', async language => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(language)
    expect(container.textContent).toContain(portabilityResources[language].scope)
    expect(container.textContent).toContain(portabilityResources[language].privacy)
    expect(container.textContent).toContain(portabilityResources[language].exclusions)
    expect(button(portabilityResources[language].create).disabled).toBe(true)
    await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })))
    expect(fetcher).not.toHaveBeenCalled(); expect(container.querySelector('input')!.checked).toBe(false)
  })
  it('confirms creation before exposing a separate verified download and restores the reference without POST', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(receipt)).mockResolvedValueOnce(packageResponse()); vi.stubGlobal('fetch', fetcher)
    await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][1].method).toBe('POST')
    expect(container.textContent).toContain('Última cópia confirmada'); expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(restoreExportState()).toEqual({ status: 'ready', receipt }); await reopen(); expect(fetcher).toHaveBeenCalledTimes(1)
    await click(portabilityResources['pt-BR'].download)
    await downloaded()
    expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls[1][1].method).toBe('GET')
    expect(URL.createObjectURL).toHaveBeenCalledOnce(); expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledOnce()
    expect(container.textContent).toContain(portabilityResources['pt-BR'].downloadSent)
    expect(container.textContent).not.toContain('PRIVATE_PACKAGE_CONTENT'); expect(sessionStorage.getItem(EXPORT_STATE_KEY)).not.toContain('PRIVATE_PACKAGE_CONTENT')
    await act(async () => root.unmount()); root = createRoot(container); expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:private-test-copy')
  })
  it('keeps lost creation uncertain across reopen and requires new consent for a genuinely new copy', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('PRIVATE_UPSTREAM_FAILURE')).mockResolvedValueOnce(Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
    await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(restoreExportState()).toEqual({ status: 'pending', previous: null }); expect(container.textContent).toContain(portabilityResources['pt-BR'].uncertain)
    expect(container.textContent).not.toContain('PRIVATE_UPSTREAM_FAILURE'); expect(container.querySelector('input')!.checked).toBe(false)
    await reopen(); expect(fetcher).toHaveBeenCalledTimes(1); expect(button(portabilityResources['pt-BR'].create).disabled).toBe(true)
    await consent(); await click(portabilityResources['pt-BR'].create); expect(fetcher).toHaveBeenCalledTimes(2); expect(restoreExportState()).toEqual({ status: 'ready', receipt })
  })
  it('preserves the last confirmed copy when a later creation is uncertain', async () => {
    rememberExportReceipt(receipt); const fetcher = vi.fn().mockRejectedValue(new TypeError('lost')); vi.stubGlobal('fetch', fetcher)
    await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(restoreExportState()).toEqual({ status: 'pending', previous: receipt }); await reopen()
    expect(container.textContent).toContain('Última cópia confirmada'); expect(button(portabilityResources['pt-BR'].download).disabled).toBe(false); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('clears the prior reference on creation401 instead of restoring it after session expiry', async () => {
    rememberExportReceipt(receipt); const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'session_required' } }, { status: 401 })); vi.stubGlobal('fetch', fetcher)
    await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(fetcher).toHaveBeenCalledOnce(); expect(restoreExportState()).toEqual({ status: 'empty' }); expect(container.querySelector('.export-receipt')).toBeNull()
    expect(container.querySelector('input')!.checked).toBe(false)
  })
  it('blocks creation before HTTP when the pending reference cannot be persisted', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => null, setItem: () => { throw new Error('blocked') } })
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(fetcher).not.toHaveBeenCalled(); expect(container.textContent).toContain(portabilityResources['pt-BR'].storageError); expect(container.querySelector('input')!.checked).toBe(false)
  })
  it('retains confirmed metadata in memory if storing the response fails after HTTP', async () => {
    const storage = sessionStorage; vi.stubGlobal('sessionStorage', { getItem: (key: string) => storage.getItem(key), removeItem: (key: string) => storage.removeItem(key), setItem: (key: string, value: string) => { if (value.includes('"status":"ready"')) throw new Error('blocked'); storage.setItem(key, value) } })
    const fetcher = vi.fn().mockResolvedValue(Response.json(receipt)); vi.stubGlobal('fetch', fetcher); await render(); await consent(); await click(portabilityResources['pt-BR'].create)
    expect(container.textContent).toContain(portabilityResources['pt-BR'].storageWarning); expect(container.textContent).toContain('Última cópia confirmada'); expect(button(portabilityResources['pt-BR'].download).disabled).toBe(false)
    expect(restoreExportState()).toEqual({ status: 'pending', previous: null })
  })
  it('serializes duplicate clicks and ignores a late response after the panel closes', async () => {
    let resolve!: (value: Response) => void
    const fetcher = vi.fn((url: string, options: RequestInit) => { expect(url).toBe('/api/v1/portability/exports'); expect(options.method).toBe('POST'); return new Promise<Response>(done => { resolve = done }) }); vi.stubGlobal('fetch', fetcher)
    await render(); await consent(); await act(async () => { button(portabilityResources['pt-BR'].create).click(); container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })) })
    expect(fetcher).toHaveBeenCalledTimes(1); expect(onBusy).toHaveBeenCalledWith(true); expect(button(portabilityResources['pt-BR'].back).disabled).toBe(true)
    const signal = fetcher.mock.calls[0][1].signal!; await act(async () => root.unmount()); root = createRoot(container); expect(signal.aborted).toBe(true)
    await act(async () => resolve(Response.json(receipt))); expect(restoreExportState()).toEqual({ status: 'pending', previous: null }); expect(URL.createObjectURL).not.toHaveBeenCalled()
  })
  it('rejects tampered downloads without losing the confirmed copy and retries GET only on demand', async () => {
    rememberExportReceipt(receipt); const bad = bytes.slice(); bad[0] ^= 1
    const fetcher = vi.fn().mockResolvedValueOnce(packageResponse(bad)).mockResolvedValueOnce(packageResponse()); vi.stubGlobal('fetch', fetcher)
    await render(); await click(portabilityResources['pt-BR'].download)
    await downloaded()
    expect(container.textContent).toContain(portabilityResources['pt-BR'].integrityError); expect(URL.createObjectURL).not.toHaveBeenCalled(); expect(restoreExportState()).toEqual({ status: 'ready', receipt })
    await click(portabilityResources['pt-BR'].download); await downloaded(); expect(fetcher.mock.calls.map(call => call[1].method)).toEqual(['GET', 'GET']); expect(URL.createObjectURL).toHaveBeenCalledOnce()
  })
  it('treats corrupt saved state as an error until explicitly forgotten, without touching the server', async () => {
    sessionStorage.setItem(EXPORT_STATE_KEY, JSON.stringify({ version: 1, status: 'ready', receipt: { ...receipt, id: '<script>private</script>' } }))
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(); expect(container.textContent).toContain(portabilityResources['pt-BR'].stateError); expect(container.querySelector('form')).toBeNull(); expect(container.innerHTML).not.toContain('<script>')
    await click(portabilityResources['pt-BR'].forget); expect(restoreExportState()).toEqual({ status: 'empty' }); expect(fetcher).not.toHaveBeenCalled(); expect(button(portabilityResources['pt-BR'].create).disabled).toBe(true)
  })
  it('forgets only local metadata, including an uncertain marker, without deleting a server copy', async () => {
    rememberExportPending(receipt); const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher); await render(); await click(portabilityResources['pt-BR'].forget)
    expect(restoreExportState()).toEqual({ status: 'empty' }); expect(fetcher).not.toHaveBeenCalled(); expect(URL.createObjectURL).not.toHaveBeenCalled()
  })
})
