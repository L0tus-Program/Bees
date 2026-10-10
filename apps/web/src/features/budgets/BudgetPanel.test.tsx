import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { BudgetSummary } from '../../api/budgets'
import { budgetResources } from '../../locales/budgets'
import { BudgetSummaryView } from './BudgetPanel'

const limit = { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active' as const, revision: 1, updated_at: '2026-10-09T18:00:00+00:00' }
const summary: BudgetSummary = { limit, window_seconds: 86400, counted_tokens: 1500, reported_tokens: 1200, remaining_tokens: 8500, open_reservations: 1, unknown_entries: 2, entries: 5 }

async function render(value: BudgetSummary, language = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: 'pt-BR', resources: { 'pt-BR': { budgets: budgetResources['pt-BR'] }, en: { budgets: budgetResources.en }, es: { budgets: budgetResources.es } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}><BudgetSummaryView summary={value} /></I18nextProvider>)
}

describe('budget summary', () => {
  it('separates counted, reported and uncertain usage', async () => {
    const html = await render(summary)
    for (const label of ['Consumo contado', 'Informado pelo provedor', 'Registros incertos', 'Chamadas em andamento', '24 horas (janela móvel)', 'Restante']) expect(html).toContain(label)
    expect(html).toContain('pode ter cobrado')
    expect(html).not.toContain('Limite atingido')
  })
  it('explains the concrete path when the limit is exhausted', async () => {
    const html = await render({ ...summary, remaining_tokens: 0 })
    expect(html).toContain('Limite atingido')
    expect(html).toContain('Aumente o limite ou aguarde a janela')
  })
  it('never shows remaining budget without an active limit', async () => {
    const none = await render({ ...summary, limit: null, remaining_tokens: null, unknown_entries: 0 })
    expect(none).toContain('Sem limite configurado')
    expect(none).not.toContain('Restante')
    expect(none).not.toContain('pode ter cobrado')
    const disabled = await render({ ...summary, limit: { ...limit, status: 'disabled' }, remaining_tokens: null })
    expect(disabled).toContain('Limite desativado')
    expect(disabled).not.toContain('Restante')
  })
  it('localizes english and spanish labels and plural windows', async () => {
    expect(await render({ ...summary, window_seconds: 604800, limit: { ...limit, window_seconds: 604800 } }, 'en')).toContain('7 days (rolling)')
    expect(await render(summary, 'es')).toContain('Informado por el proveedor')
    expect(await render({ ...summary, window_seconds: 3600, limit: { ...limit, window_seconds: 3600 } }, 'pt-BR')).toContain('1 hora (janela móvel)')
  })
})
