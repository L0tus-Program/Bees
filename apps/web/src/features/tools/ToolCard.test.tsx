import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { PluginRecord, ToolRecord } from '../../api/tools'
import { toolResources } from '../../locales/tools'
import { ToolCard } from './ToolCard'
import { ToolConfirmation } from './ToolConfirmation'

const i18n = createInstance()
await i18n.init({ lng: 'pt-BR', fallbackLng: 'pt-BR', resources: { 'pt-BR': { tools: toolResources['pt-BR'] }, en: { tools: toolResources.en }, es: { tools: toolResources.es } }, interpolation: { escapeValue: false } })
const plugin: PluginRecord = { id: 'plugin-1', manifest: { manifest_version: 1, plugin_key: 'bees.text', name: 'Texto local', version: '1.0.0', tools: [{ tool_name: 'text.normalize', version: '1', executor: 'builtin:text.normalize@1' }] }, manifest_hash: 'hash', enabled: false, revision: 1, created_at: '2026-10-06T18:00:00Z', updated_at: '2026-10-06T18:00:00Z' }
const tool: ToolRecord = { plugin_id: plugin.id, plugin_name: plugin.manifest.name, plugin_version: plugin.manifest.version, plugin_enabled: false, plugin_revision: 1, tool_name: 'text.normalize', version: '1', capabilities: ['pure_text'], input_schema: { type: 'object' }, output_schema: { type: 'object' }, grant_id: null, grant_revision: 0, granted: false, available: false }
function render(granted = false, enabled = false, disabled = false) {
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}><ToolCard plugin={{ ...plugin, enabled }} tools={[{ ...tool, granted, plugin_enabled: enabled }]} agentName="Aurora" disabled={disabled} onGrantChange={() => {}} onPluginChange={() => {}} /></I18nextProvider>)
}

describe('tool access clarity', () => {
  it('keeps global activation and per-bee access separate', () => {
    const html = render()
    for (const label of ['Desabilitada na colmeia', 'Uso não concedido', 'Habilitar na colmeia', 'Conceder uso', 'Uso por Aurora', 'sem acesso a arquivos ou rede']) expect(html).toContain(label)
    expect(html).not.toContain('Executar')
    expect(html).not.toContain('Confirmar alteração')
  })
  it('shows a preserved grant as unavailable while the global tool is disabled', () => {
    const html = render(true)
    expect(html).toContain('Uso concedido')
    expect(html).toContain('Indisponível enquanto desabilitada')
    expect(html).toContain('Revogar uso')
    expect(html).not.toContain('Conceder uso')
  })
  it('offers disable and revoke separately when both states are on', () => {
    const html = render(true, true)
    expect(html).toContain('Desabilitar na colmeia')
    expect(html).toContain('Revogar uso')
  })
  it('disables mutations while state is stale, loading or changing', () => {
    expect(render(false, false, true).match(/disabled=""/g)).toHaveLength(2)
  })
  it('escapes manifest names and schemas as data', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><ToolCard plugin={{ ...plugin, manifest: { ...plugin.manifest, name: '<script>allow()</script>' } }} tools={[{ ...tool, input_schema: { description: '<img src=x onerror=grant()>' } }]} agentName="Aurora" disabled={false} onGrantChange={() => {}} onPluginChange={() => {}} /></I18nextProvider>)
    expect(html).toContain('&lt;script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
  })
  it('explains global disable impact before confirmation and blocks stale confirmation', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><ToolConfirmation change={{ kind: 'plugin', plugin, enabled: false }} agentName="Aurora" busy={false} blocked onConfirm={() => {}} onCancel={() => {}} /></I18nextProvider>)
    expect(html).toContain('para todas as abelhas')
    expect(html).toContain('uma ação já iniciada não é desfeita')
    expect(html).toContain('disabled=""')
  })
  it('explains that a bee grant does not replace autonomy policies', () => {
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><ToolConfirmation change={{ kind: 'grant', tool, enabled: true }} agentName="Aurora" busy={false} blocked={false} onConfirm={() => {}} onCancel={() => {}} /></I18nextProvider>)
    expect(html).toContain('Aurora')
    expect(html).toContain('não substitui as regras de Autonomia')
    expect(html).toContain('Confirmar alteração')
  })
  it.each([['en', 'Disabled in the hive', 'Grant use', 'Use by Aurora'], ['es', 'Desactivada en la colmena', 'Conceder uso', 'Uso por Aurora']])('translates access controls without hiding exact tool identity in %s', async (language, globalState, action, access) => {
    await i18n.changeLanguage(language)
    try {
      const html = render()
      for (const label of [globalState, action, access, 'text.normalize']) expect(html).toContain(label)
      expect(html).not.toContain('Desabilitada na colmeia')
    } finally { await i18n.changeLanguage('pt-BR') }
  })
})
