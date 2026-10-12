import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ActivityPanel } from './ActivityPanel'
import { ActivitySummary, ActivityTimeline } from './ActivityTimeline'
import { activityResources } from '../../locales/activity'
import { budgetResources } from '../../locales/budgets'
import { productResources } from '../../locales/product'
import { actionId, agent, item, page } from './fixtures'
async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { activity: activityResources[language], budgets: budgetResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('operational activity presentation', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('separates token certainty, operations and estimate source in %s', async (language) => {
    const html = await render(<ActivitySummary page={page} />, language)
    for (const key of ['reported', 'estimated', 'reserved', 'unknown', 'model_calls_in_flight', 'unknown_model_calls', 'waiting_resource', 'chars_div_3_v1', 'resolution'] as const) expect(html).toContain(activityResources[language][key])
    expect(html).not.toContain('USD'); expect(html).not.toContain('R$')
  })
  it.each(['pt-BR', 'en', 'es'] as const)('keeps native controls, factual environment and timestamps in %s', async (language) => {
    const html = await render(<ActivityTimeline items={[{ ...item, action_id: actionId }]} disabled={false} onOpen={() => {}} onScope={() => {}} />, language)
    expect(html).toMatch(/date[Tt]ime="2026-10-12T11:00:00Z"/)
    expect(html).toContain(activityResources[language].environment_textual); expect(html).toContain(activityResources[language].openTask); expect(html).toContain(activityResources[language].filterRun)
    expect(html).toContain(actionId); expect(html).not.toContain('<a '); expect(html).not.toContain('<iframe')
  })
  it('keeps prior facts visible while disabling actions on stale/loading snapshots', async () => {
    const html = await render(<ActivityTimeline items={[item]} disabled onOpen={() => {}} onScope={() => {}} />)
    expect(html).toContain(item.task_title); expect(html.match(/disabled=""/g)).toHaveLength(3)
  })
  it.each(['pt-BR', 'en', 'es'] as const)('prioritizes unknown outcome over any provider error that suggests resending in %s', async (language) => {
    const html = await render(<ActivityTimeline items={[{ ...item, entity_type: 'model_call', status: 'outcome_unknown', error_code: 'provider_unavailable' }]} disabled={false} onOpen={() => {}} onScope={() => {}} />, language)
    expect(html).toContain(activityResources[language].unknownHelp)
    expect(html).toContain('<code>provider_unavailable</code>')
    expect(html).not.toContain(productResources[language].errors.provider_unavailable)
  })
  it('escapes task titles and does not imply an unassociated environment is a VM', async () => {
    const html = await render(<ActivityTimeline items={[{ ...item, task_title: '<script>unsafe</script>', environment: { kind: 'unassociated', environment_id: null } }]} disabled={false} onOpen={() => {}} onScope={() => {}} />)
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>'); expect(html).toContain(activityResources['pt-BR'].environment_unassociated)
  })
  it.each(['pt-BR', 'en', 'es'] as const)('starts loading without inventing an empty timeline or free budget in %s', async (language) => {
    const html = await render(<ActivityPanel agent={agent} onPreferences={() => {}} />, language)
    expect(html).toContain(activityResources[language].loading); expect(html).toContain(activityResources[language].preferences)
    expect(html).not.toContain(activityResources[language].empty); expect(html).not.toContain(budgetResources[language].noLimit)
  })
})
