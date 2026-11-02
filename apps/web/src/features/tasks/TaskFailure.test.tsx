import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { expect, it } from 'vitest'
import { productResources } from '../../locales/product'
import { workspaceResources } from '../../locales/workspace'
import { task } from '../chat/testFixtures'
import { TaskFailure } from './TaskFailure'

it.each(['pt-BR', 'en', 'es'] as const)('explains a model failure even with unknown outcome in %s', async (language) => {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { workspace: workspaceResources[language], product: productResources[language] } }, interpolation: { escapeValue: false } })
  const failed = { ...task, status: 'paused' as const, unknown_requires_ack: true, unknown_model_calls: 1, latest_run: { ...task.latest_run!, model: 'gpt-5.6-terra', error_code: 'outcome_unknown', failure: { code: 'tool_schema_rejected', http_status: 400, parameter: 'tools.function.parameters' } } }
  const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><TaskFailure task={failed} warnUnknown /></I18nextProvider>)
  expect(html).toContain(i18n.t('errors.tool_schema_rejected', { ns: 'product' }))
  expect(html).toContain(i18n.t('modelOutcomeUnknown', { ns: 'workspace' }))
  for (const evidence of ['gpt-5.6-terra', '400', 'tools.function.parameters', 'tool_schema_rejected']) expect(html).toContain(evidence)
  expect(html).not.toContain('<button')
})
