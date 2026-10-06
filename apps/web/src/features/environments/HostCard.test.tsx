import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { PairedHost } from '../../api/environments'
import { environmentResources } from '../../locales/environments'
import { HostCard } from './HostCard'
import { HostConfirmation } from './HostConfirmation'

const now = Date.parse('2026-10-06T18:00:00Z')
const host: PairedHost = {
  host_id: 'edcbab19-f977-408b-a4d9-b41d6ee4d85a', installation_id: 'e1d1c19f-48a0-4c94-ad30-ac4b833691ad', status: 'pending', revision: 1, report_revision: 0,
  fingerprint: 'ABCD-EF01-2345-6789', created_at: new Date(now).toISOString(), paired_at: null, last_seen: null, pairing_expires_at: new Date(now + 900_000).toISOString(),
  diagnostic: null, online: false, confirmable: true, provisionable: false,
}
async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { environments: environmentResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('host diagnostic interaction states', () => {
  it('shows the comparison code and offers confirmation without claiming a machine is available', async () => {
    const html = await render(<HostCard host={host} now={now} disabled={false} unverified={false} onChoose={() => {}} />)
    expect(html).toContain('ABCD-EF01-2345-6789'); expect(html).toContain('Aguardando sua confirmação'); expect(html).toContain('Vincular para diagnóstico')
    expect(html).not.toContain('Disponível'); expect(html).not.toContain('input type="password"')
  })
  it('requires explicit code comparison before enabling the final diagnostic-only confirmation', async () => {
    const html = await render(<HostConfirmation change={{ host, operation: 'confirm', requestId: 'request-1' }} now={now} busy={false} blocked={false} onConfirm={() => {}} onCancel={() => {}} />)
    expect(html).toContain('type="checkbox"'); expect(html).toContain('Comparei o código com o launcher deste host.'); expect(html).toContain('sem comandos de execução ou informações pessoais')
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Confirmar vínculo de diagnóstico/)
  })
  it('allows revocation of an expired pending request without offering pairing', async () => {
    const html = await render(<HostCard host={host} now={now + 900_000} disabled={false} unverified={false} onChoose={() => {}} />)
    expect(html).toContain('Pedido de vínculo expirado'); expect(html).toContain('Revogar vínculo'); expect(html).not.toContain('Vincular para diagnóstico')
  })
  it('shows old diagnostics as offline when the API state is unverified', async () => {
    const active: PairedHost = { ...host, status: 'active', revision: 2, report_revision: 1, paired_at: new Date(now).toISOString(), last_seen: new Date(now).toISOString(), online: true, confirmable: false,
      diagnostic: { driver: 'hyperv', status: 'driver_unavailable', probe: { platform: true, module: false, service: false }, provisionable: false } }
    const html = await render(<HostCard host={active} now={now} disabled={true} unverified={true} onChoose={() => {}} />)
    expect(html).toContain('Helper sem diagnóstico recente'); expect(html).toContain('Último diagnóstico:'); expect(html).toContain('Indisponível'); expect(html).not.toContain('Vínculo ativo')
  })
  it.each(['en', 'es'] as const)('translates the code-comparison confirmation in %s', async (language) => {
    const html = await render(<HostConfirmation change={{ host, operation: 'confirm', requestId: 'request-1' }} now={now} busy={false} blocked={false} onConfirm={() => {}} onCancel={() => {}} />, language)
    expect(html).toContain(language === 'en' ? 'Confirm diagnostic pairing' : 'Confirmar vínculo de diagnóstico')
    expect(html).not.toContain('pairCompared'); expect(html).not.toContain('pairImpact')
  })
})
