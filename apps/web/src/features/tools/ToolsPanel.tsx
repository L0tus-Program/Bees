import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { installPlugin, updatePlugin, updateToolGrant } from '../../api/tools'
import type { ToolManifest } from '../../api/tools'
import { ErrorNotice } from '../../components/Feedback'
import { installationIdentity, loadTools, parseManifest, uncertainMutation } from './state'
import type { ToolWorkspace } from './state'
import { ToolCard } from './ToolCard'
import { ToolConfirmation } from './ToolConfirmation'
import type { ToolChange } from './ToolConfirmation'

export function ToolsPanel({ agent }: { agent: AgentSummary }) {
  const { t, i18n } = useTranslation('tools')
  const [data, setData] = useState<ToolWorkspace | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [change, setChange] = useState<ToolChange | null>(null)
  const [manifestText, setManifestText] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [identify] = useState(installationIdentity)
  const read = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController(); read.current = controller
    void loadTools(agent.id, controller.signal).then((workspace) => {
      if (!controller.signal.aborted) { setData(workspace); setLoading(false); setError(null) }
    }, (failure: unknown) => {
      if (!controller.signal.aborted) { setError(asApiError(failure)); setLoading(false) }
    })
    return () => controller.abort()
  }, [agent.id, attempt])
  useEffect(() => () => mutation.current?.abort(), [])
  const refresh = () => {
    read.current?.abort(); setLoading(true); setChange(null); setError(null); setAttempt((value) => value + 1)
  }
  async function mutate(operation: (signal: AbortSignal) => Promise<unknown>, success: string) {
    if (busy || loading || error) return
    const controller = new AbortController(); mutation.current = controller
    setBusy(true); setError(null); setNotice(null)
    try {
      await operation(controller.signal)
      if (!controller.signal.aborted) { setChange(null); setNotice(success); refresh() }
    } catch (failure) {
      if (!controller.signal.aborted) {
        const issue = asApiError(failure); setError(issue)
        if (uncertainMutation(issue)) { setChange(null); setNotice('uncertain') }
        else if (issue.status === 409) { setChange(null); setNotice('conflict') }
      }
    } finally { if (!controller.signal.aborted) setBusy(false) }
  }
  function install(manifest: ToolManifest) {
    const id = identify(manifest)
    void mutate((signal) => installPlugin(manifest, id, signal), 'installedSuccess')
  }
  function importManifest(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    try { install(parseManifest(manifestText)) } catch (failure) { setError(asApiError(failure)) }
  }
  function confirmChange() {
    if (!change) return
    void mutate((signal) => change.kind === 'plugin' ? updatePlugin(change.plugin, change.enabled, signal) : updateToolGrant(agent.id, change.tool, change.enabled, signal), 'saved')
  }
  const locked = loading || busy || error !== null || change !== null
  const localError = error && ['manifestInvalid', 'manifestSize'].includes(error.code)
  const knownError = error && i18n.exists(`error_${error.code}`, { ns: 'tools' })
  return <section className="surface tools-panel" aria-labelledby="tools-heading" aria-busy={loading || busy}>
    <div className="form-heading"><h2 id="tools-heading">{t('title')}</h2><button className="button secondary" type="button" disabled={loading || busy} onClick={refresh}>{t('refresh')}</button></div>
    <p className="form-introduction">{t('description')}</p><p className="transfer-notice">{t('independence')}</p><p className="quiet-note">{t('scope')}</p>
    {notice && <p className={['uncertain', 'conflict'].includes(notice) ? 'form-error' : 'form-success'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {error && <>{localError || knownError ? <p className="form-error" role="alert">{t(localError ? error.code : `error_${error.code}`)}</p> : <ErrorNotice error={error} />}{data && !localError && <p className="quiet-note">{t('stale')}</p>}</>}
    {change && <ToolConfirmation change={change} agentName={agent.name} busy={busy} blocked={loading || error !== null} onConfirm={confirmChange} onCancel={() => setChange(null)} />}
    {data && <>
      <div className="tools-section-heading"><h3>{t('installed')}</h3></div>
      {data.plugins.length === 0 && <div className="tool-empty"><p>{t('empty')}</p><p>{t('emptyHelp')}</p></div>}
      <div className="tool-list">{data.plugins.map((plugin) => <ToolCard key={plugin.id} plugin={plugin} tools={data.tools.filter((tool) => tool.plugin_id === plugin.id)} agentName={agent.name} disabled={locked} onPluginChange={(enabled) => { setChange({ kind: 'plugin', plugin, enabled }); setNotice(null) }} onGrantChange={(tool, enabled) => { setChange({ kind: 'grant', tool, enabled }); setNotice(null) }} />)}</div>
      <div className="tools-section-heading"><h3>{t('catalog')}</h3></div>
      {data.catalog.length === 0 && <p className="quiet-note">{t('catalogEmpty')}</p>}
      <div className="tool-list">{data.catalog.map((manifest) => {
        const installed = data.plugins.some((plugin) => plugin.manifest.plugin_key === manifest.plugin_key)
        return <article className="tool-catalog-card" key={manifest.plugin_key}><div><h4>{manifest.name}</h4><p className="quiet-note">{t('version', { version: manifest.version })}</p><p className="tool-scope">{manifest.tools.map((tool) => tool.tool_name).join(' · ')}</p></div><button className="button secondary" type="button" disabled={locked || installed} onClick={() => install(manifest)}>{t(installed ? 'installedLabel' : busy ? 'installing' : 'install')}</button></article>
      })}</div>
      <details className="tool-manifest"><summary>{t('manifest')}</summary><form onSubmit={importManifest}><label htmlFor="tool-manifest-json">{t('manifestLabel')}</label><p id="tool-manifest-help" className="quiet-note">{t('manifestHelp')}</p><textarea id="tool-manifest-json" aria-describedby="tool-manifest-help" required rows={9} spellCheck={false} value={manifestText} disabled={busy || loading || change !== null} onChange={(event) => { setManifestText(event.target.value); if (localError || error && ['invalid_manifest', 'unknown_tool'].includes(error.code)) setError(null) }} /><button className="button secondary" type="submit" disabled={locked || !manifestText.trim()}>{t(busy ? 'installing' : 'import')}</button></form></details>
    </>}
  </section>
}
