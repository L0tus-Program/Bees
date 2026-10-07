import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { host, plan } from '../../api/provisioning.fixtures'
import { provisioningResources } from '../../locales/provisioning'
import { ProvisioningConfirmation, ProvisioningPlanSummary, ProvisioningPlanValidity } from './ProvisioningReview'

const state = { plan, host, preparation_available: true }
async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { provisioning: provisioningResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('human provisioning review', () => {
  it('presents the concrete resources, selected host and safe creation scope without credentials or setup commands', async () => {
    const html = await render(<ProvisioningPlanSummary plan={plan} state={state} />)
    expect(html).toContain('Criar um computador próprio desligado'); expect(html).toContain('2 CPU · 4 GiB de memória · 30 GiB de disco')
    expect(html).toContain(host.fingerprint); expect(html).toContain('sem rede'); expect(html).toContain('sem iniciar o computador'); expect(html).toContain('Identificação da ISO')
    expect(html).not.toContain('<input'); expect(html).not.toContain('vm_name'); expect(html).not.toContain('powershell')
  })
  it('requires explicit human review before confirming a single plan authorization', async () => {
    const html = await render(<ProvisioningConfirmation action="authorize" plan={plan} state={state} checked={false} busy={false} blocked={false} onChecked={() => {}} onConfirm={() => {}} onBack={() => {}} />)
    expect(html.match(/type="checkbox"/g)).toHaveLength(1); expect(html).toContain('expira em 15 minutos')
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Confirmar autorização deste plano/)
  })
  it('keeps stale context blocked even after the user checked the review box', async () => {
    const html = await render(<ProvisioningConfirmation action="authorize" plan={plan} state={state} checked={true} busy={false} blocked={true} onChecked={() => {}} onConfirm={() => {}} onBack={() => {}} />)
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Confirmar autorização deste plano/)
  })
  it('revokes without granting another action or claiming in-flight effects are undone', async () => {
    const html = await render(<ProvisioningConfirmation action="revoke" plan={plan} state={state} checked={false} busy={false} blocked={false} onChecked={() => {}} onConfirm={() => {}} onBack={() => {}} />)
    expect(html).not.toContain('type="checkbox"'); expect(html).toContain('efeitos que a revogação não desfaz'); expect(html).toContain('Confirmar revogação')
  })
  it('stops claiming authorization is valid after revocation even while the historical deadline remains in the journal', async () => {
    const historical = { ...plan, revision: 3, authorization_expires_at: '2026-10-07T18:15:00Z' }
    const now = Date.parse('2026-10-07T18:10:00Z')
    const active = await render(<ProvisioningPlanValidity plan={{ ...historical, status: 'authorized' }} now={now} />)
    const revoked = await render(<ProvisioningPlanValidity plan={{ ...historical, status: 'revoked' }} now={now} />)
    expect(active).toContain('Autorização válida até'); expect(revoked).not.toContain('Autorização válida até')
    expect(revoked).not.toContain('autorizar novamente')
  })
  it('preserves the deadline and expiry warning for an expired authorization requiring a new human review', async () => {
    const html = await render(<ProvisioningPlanValidity plan={{ ...plan, status: 'authorized', authorization_expires_at: '2026-10-07T18:15:00Z' }} now={Date.parse('2026-10-07T18:15:00Z')} />)
    expect(html).toContain('Autorização válida até'); expect(html).toContain('A autorização expirou')
  })
  it.each(['en', 'es'] as const)('translates the concrete scope in %s', async (language) => {
    const html = await render(<ProvisioningPlanSummary plan={plan} state={state} />, language)
    expect(html).toContain(language === 'en' ? 'powered-off state' : 'ordenador propio apagado'); expect(html).not.toContain('imageDescription')
  })
})
