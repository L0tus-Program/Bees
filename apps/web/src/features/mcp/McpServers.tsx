import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createMcpServer, listMcpServers, refreshMcpServer, removeMcpServer, setMcpServerEnabled } from '../../api/mcp'
import type { McpServer } from '../../api/mcp'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

export function McpServers({ agentId }: { agentId: string }) {
  const { t } = useTranslation('mcp')
  const [servers, setServers] = useState<McpServer[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const load = useCallback(async (signal?: AbortSignal) => {
    try { setServers(await listMcpServers(agentId, signal)) } catch (failure) { if (!signal?.aborted) setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load])
  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await action(); await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await act(async () => {
      await createMcpServer(agentId, { client_request_id: requestId, name: name.trim(), url: url.trim(), ...(token.trim() ? { token: token.trim() } : {}) })
      setName(''); setUrl(''); setToken(''); setRequestId(crypto.randomUUID())
    })
  }
  return (
    <section className="surface mcp-servers" aria-labelledby="mcp-title">
      <h2 id="mcp-title">{t('title')}</h2>
      <p className="quiet-note">{t('scope')}</p>
      {error && <ErrorNotice error={error} />}
      {servers?.length === 0 && <p>{t('empty')}</p>}
      <ul className="channel-list">
        {servers?.map((server) => <li key={server.id} className="channel-card">
          <div className="channel-heading"><strong>{server.name}</strong>{server.server_name && <small>{server.server_name}</small>}<span>{server.enabled ? t(`status_${server.status}`) : t('disabled')}</span><span>{t('tools', { count: server.tools.length })}</span></div>
          <code>{server.url}</code>
          {server.reason_code && <p className="transfer-notice">{t(`reason_${server.reason_code}`, { defaultValue: t('reason_other', { code: server.reason_code }) })}</p>}
          <details><summary>{t('tools', { count: server.tools.length })}</summary><ul>{server.tools.map((tool) => <li key={tool.name}><strong>{tool.name}</strong>{tool.read_only && <small> · {t('readOnly')}</small>}{tool.description && <small className="block">{tool.description}</small>}</li>)}</ul></details>
          <div className="row-actions">
            <button type="button" className="button secondary" disabled={busy} onClick={() => { void act(() => refreshMcpServer(server)) }}>{t('refresh')}</button>
            <button type="button" className="text-button" disabled={busy} onClick={() => { void act(() => setMcpServerEnabled(server, !server.enabled)) }}>{t(server.enabled ? 'disable' : 'enable')}</button>
            <button type="button" className="text-button danger" disabled={busy} onClick={() => { void confirmAction(t('removeConfirm')).then((ok) => { if (ok) void act(() => removeMcpServer(server)) }) }}>{t('remove')}</button>
          </div>
        </li>)}
      </ul>
      <form className="channel-form" onSubmit={(event) => { void submit(event) }}>
        <h3>{t('add')}</h3>
        <fieldset disabled={busy}>
          <label className="field" htmlFor="mcp-name"><span>{t('name')}</span><input id="mcp-name" value={name} onChange={(event) => setName(event.target.value)} required pattern="[a-z0-9][a-z0-9_\-]{0,39}" maxLength={40} aria-describedby="mcp-name-help" /><small id="mcp-name-help">{t('nameHelp')}</small></label>
          <label className="field" htmlFor="mcp-url"><span>{t('url')}</span><input id="mcp-url" type="url" value={url} onChange={(event) => setUrl(event.target.value)} required maxLength={2048} placeholder="https://" aria-describedby="mcp-url-help" /><small id="mcp-url-help">{t('urlHelp')}</small></label>
          <label className="field" htmlFor="mcp-token"><span>{t('token')}</span><input id="mcp-token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} maxLength={4096} aria-describedby="mcp-token-help" /><small id="mcp-token-help">{t('tokenHelp')}</small></label>
          <button type="submit" className="button primary">{t(busy ? 'creating' : 'create')}</button>
        </fieldset>
        <p className="quiet-note">{t('needsGrant')}</p>
      </form>
    </section>
  )
}

export default McpServers
