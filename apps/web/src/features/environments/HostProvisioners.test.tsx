import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { PairedHost } from '../../api/environments'
import type { ProvisionerRecord } from '../../api/provisioners'
import { environmentResources } from '../../locales/environments'
import { provisionerResources } from '../../locales/provisioners'
import { HostCard } from './HostCard'
import { HostProvisioners, ProvisionerConfirmation } from './HostProvisioners'

const now = Date.parse('2026-10-07T12:00:00Z')
const host: PairedHost = {
  host_id: '11111111-1111-4111-8111-111111111111', installation_id: '22222222-2222-4222-8222-222222222222', status: 'active', revision: 2, report_revision: 0,
  fingerprint: 'ABCD-EF01-2345-6789', created_at: new Date(now).toISOString(), paired_at: new Date(now).toISOString(), last_seen: null, pairing_expires_at: new Date(now + 900_000).toISOString(), diagnostic: null, online: false, confirmable: false, provisionable: false,
}
const record: ProvisionerRecord = { provisioner_id: '33333333-3333-4333-8333-333333333333', installation_id: host.installation_id, host_id: host.host_id, revision: 1, status: 'active' }
afterEach(() => vi.unstubAllGlobals())
async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { environments: environmentResources[language], provisioners: provisionerResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('human management of local provisioners', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('keeps the panel collapsed until the user asks to read registrations in %s', async (language) => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    const html = await render(<HostCard host={host} now={now} disabled={false} unverified={false} onChoose={() => {}} />, language)
    expect(html).toContain(provisionerResources[language].heading)
    expect(html).toContain(provisionerResources[language].open)
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain(provisionerResources[language].loading)
    expect(html).not.toContain(provisionerResources[language].confirm)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('disables opening for an unconfirmed host but allows reading older registrations on a revoked host', async () => {
    let html = await render(<HostCard host={{ ...host, status: 'pending', paired_at: null, confirmable: true }} now={now} disabled={false} unverified={false} onChoose={() => {}} />)
    expect(html).toMatch(/aria-expanded="false" disabled=""[^>]*>Ver cadastros/)
    html = await render(<HostCard host={{ ...host, status: 'revoked' }} now={now} disabled={false} unverified={false} onChoose={() => {}} />)
    expect(html).toMatch(/aria-expanded="false">Ver cadastros/)
  })
  it.each(['pt-BR', 'en', 'es'] as const)('makes authority status distinct from connected application or VM readiness in %s', async (language) => {
    const html = await render(<HostProvisioners host={host} disabled={false} unverified={false} />, language)
    expect(html).toContain(provisionerResources[language].scope)
    expect(html).toContain(provisionerResources[language].loading)
    expect(html).not.toContain('input type="password"')
    expect(html).not.toContain('input type="text"')
  })
  it.each(['pt-BR', 'en', 'es'] as const)('shows the exact registration and the in-flight/unknown warning before revocation in %s', async (language) => {
    const html = await render(<ProvisionerConfirmation record={record} busy={false} blocked={false} onConfirm={() => {}} onCancel={() => {}} />, language)
    expect(html).toContain(record.provisioner_id)
    expect(html).toContain(provisionerResources[language].impact)
    expect(html.indexOf(provisionerResources[language].impact)).toBeLessThan(html.indexOf(provisionerResources[language].confirm))
    expect(html).toContain(provisionerResources[language].confirm)
  })
  it('blocks confirmation when reads are stale and disables both controls while revocation is in flight', async () => {
    let html = await render(<ProvisionerConfirmation record={record} busy={false} blocked onConfirm={() => {}} onCancel={() => {}} />)
    expect(html).toMatch(/class="button danger" disabled=""[^>]*>Confirmar revogação/)
    html = await render(<ProvisionerConfirmation record={record} busy blocked={false} onConfirm={() => {}} onCancel={() => {}} />)
    expect(html.match(/disabled=""/g)).toHaveLength(2)
    expect(html).toContain(provisionerResources['pt-BR'].revoking)
  })
})
