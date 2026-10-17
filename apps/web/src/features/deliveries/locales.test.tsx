import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { deliveryResources } from '../../locales/deliveries'
import { DeliveryHistoryPanel } from './DeliveryHistoryPanel'
import { agent } from '../chat/testFixtures'

describe('delivery history languages and discoverability', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('presents native labeled search and honest shortcuts in %s', async (language) => {
    const instance = createInstance()
    await instance.init({ lng: language, resources: { [language]: { deliveries: deliveryResources[language] } }, interpolation: { escapeValue: false } })
    const html = renderToStaticMarkup(<I18nextProvider i18n={instance}><DeliveryHistoryPanel agent={agent} onSkills={() => {}} /></I18nextProvider>)
    for (const name of ['title', 'query', 'conversation', 'task', 'search', 'skills', 'routines', 'shortcutHelp', 'loading', 'cancel'] as const) expect(html).toContain(deliveryResources[language][name])
    expect(deliveryResources[language].navigation.length).toBeGreaterThan(0)
    expect(html).toContain('<form'); expect(html).toContain('type="submit"'); expect(html.match(/<label/g)).toHaveLength(3)
    expect(html).not.toContain(deliveryResources[language].empty); expect(html).not.toContain('Fora deste recorte')
  })
})
