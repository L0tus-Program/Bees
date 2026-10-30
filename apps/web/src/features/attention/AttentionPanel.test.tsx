import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { AttentionItem } from '../../api/attention'
import { attentionResources } from '../../locales/attention'
import { AttentionList } from './AttentionPanel'

const attentionFixture: AttentionItem = {
  id: '20000000-0000-4000-8000-000000000001', agent_id: '10000000-0000-4000-8000-000000000001', task_id: '30000000-0000-4000-8000-000000000001', task_title: 'Comparar alternativas', approval_id: null,
  kind: 'task_completed', created_at: '2026-10-12T18:00:00Z', revision: 1, read: false, requires_attention: true, muted: false,
}

async function render(items: AttentionItem[], language = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { 'pt-BR': { attention: attentionResources['pt-BR'] }, en: { attention: attentionResources.en }, es: { attention: attentionResources.es } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}><AttentionList items={items} onOpen={() => {}} onRead={() => {}} /></I18nextProvider>)
}
describe('attention presentation', () => {
  it('keeps muted history and separates reading from resolution', async () => {
    const html = await render([{ ...attentionFixture, muted: true }])
    expect(html).toContain('Tarefa concluída'); expect(html).toContain('Aviso silenciado'); expect(html).toContain('Abrir tarefa')
    expect(html).toContain('Marcar como lido'); expect(html).not.toContain('allow_once')
  })
  it('does not ask for a decision that was resolved or expired', async () => {
    const html = await render([{ ...attentionFixture, kind: 'approval_required', approval_id: attentionFixture.id, requires_attention: false }])
    expect(html).toContain('Decisão encerrada ou indisponível'); expect(html).not.toContain('Precisa de atenção')
  })
  it('keeps uncertainty visible after reading', async () => {
    const html = await render([{ ...attentionFixture, kind: 'outcome_unknown', read: true }])
    expect(html).toContain('O efeito pode ter ocorrido'); expect(html).toContain('Marcar como não lido'); expect(html).toContain('Precisa de atenção')
  })
  it('escapes task titles and does not keep a read result marked as pending', async () => {
    const html = await render([{ ...attentionFixture, task_title: '<script>alert(1)</script>', read: true }])
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>'); expect(html).not.toContain('Precisa de atenção')
  })
  it.each([['en', 'Task completed', 'Open task'], ['es', 'Tarea completada', 'Abrir tarea']])('renders actionable labels in %s', async (language, title, action) => {
    const html = await render([attentionFixture], language)
    expect(html).toContain(title); expect(html).toContain(action); expect(html).toContain('type="button"')
  })
})
