import { useTranslation } from 'react-i18next'
import type { PluginRecord, ToolRecord } from '../../api/tools'

export function ToolCard({ plugin, tools, agentName, disabled, onPluginChange, onGrantChange }: {
  plugin: PluginRecord; tools: ToolRecord[]; agentName: string; disabled: boolean
  onPluginChange: (enabled: boolean) => void; onGrantChange: (tool: ToolRecord, enabled: boolean) => void
}) {
  const { t } = useTranslation('tools')
  return <article className="tool-card">
    <div className="tool-card-heading"><div><h3>{plugin.manifest.name}</h3><p className="quiet-note">{t('version', { version: plugin.manifest.version })}</p></div><span className={`tool-status ${plugin.enabled ? 'enabled' : ''}`}>{t(plugin.enabled ? 'enabled' : 'disabled')}</span></div>
    <div className="tool-actions"><button className="button secondary" type="button" disabled={disabled} onClick={() => onPluginChange(!plugin.enabled)}>{t(plugin.enabled ? 'disable' : 'enable')}</button></div>
    <div className="tool-capabilities">{tools.map((tool) => <section className="tool-access" key={tool.tool_name} aria-label={`${tool.tool_name} · ${t('access', { name: agentName })}`}>
      <div className="tool-card-heading"><h4><code>{tool.tool_name}</code></h4><span className="tool-status">{t(tool.granted ? 'granted' : 'notGranted')}</span></div>
      <p className="quiet-note">{t('access', { name: agentName })}{tool.granted && !plugin.enabled && <> · {t('unavailable')}</>}</p>
      <p className="tool-scope">{tool.capabilities.map((capability) => t(capability, { defaultValue: capability })).join(' · ')}</p>
      <div className="tool-actions"><button className={`button ${tool.granted ? 'secondary' : 'primary'}`} type="button" disabled={disabled} onClick={() => onGrantChange(tool, !tool.granted)}>{t(tool.granted ? 'revoke' : 'grant')}</button></div>
      <details className="tool-contract"><summary>{t('technical')}</summary><p><strong>{t('input')}</strong></p><pre>{JSON.stringify(tool.input_schema, null, 2)}</pre><p><strong>{t('output')}</strong></p><pre>{JSON.stringify(tool.output_schema, null, 2)}</pre></details>
    </section>)}</div>
  </article>
}
