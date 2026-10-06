import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { PluginRecord, ToolRecord } from '../../api/tools'

export type ToolChange = { kind: 'plugin'; plugin: PluginRecord; enabled: boolean } | { kind: 'grant'; tool: ToolRecord; enabled: boolean }
export function ToolConfirmation({ change, agentName, busy, blocked, onConfirm, onCancel }: {
  change: ToolChange; agentName: string; busy: boolean; blocked: boolean; onConfirm: () => void; onCancel: () => void
}) {
  const { t } = useTranslation('tools')
  const ref = useRef<HTMLHeadingElement>(null)
  useEffect(() => { ref.current?.focus() }, [change])
  const action = change.kind === 'plugin' ? change.enabled ? 'enable' : 'disable' : change.enabled ? 'grant' : 'revoke'
  const title = change.kind === 'plugin' ? change.plugin.manifest.name : `${change.tool.tool_name} · ${agentName}`
  return <div className="tool-confirmation">
    <h3 ref={ref} tabIndex={-1}>{t(action)} · {title}</h3><p>{t(`${action}Impact`)}</p>
    <div className="tool-actions"><button className="button secondary" type="button" disabled={busy} onClick={onCancel}>{t('cancel')}</button><button className={`button ${change.enabled ? 'primary' : 'danger'}`} type="button" disabled={busy || blocked} onClick={onConfirm}>{t(busy ? 'saving' : 'confirm')}</button></div>
  </div>
}
