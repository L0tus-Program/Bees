import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { decideAgentApproval, getAgentApproval, listAgentApprovals, revokeAgentApproval } from '../../api/agent'
import type { AgentApproval, AgentDecision } from '../../api/agent'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

function newId() { return crypto.randomUUID() }
const BROAD = new Set(['computer', 'personal'])
const BROAD_TOOLS = /(shell|mcp\.call|browser\.(type|click|press))/
const STALE = new Set(['approval_stale', 'approval_revoked', 'approval_denied', 'approval_consumed', 'target_changed', 'tool_not_granted', 'tool_disabled', 'policy_denied', 'global_stop'])

function staleText(t: (key: string) => string, reason: string | null) {
  return t(`stale.${reason && STALE.has(reason) ? reason : 'other'}`)
}

export function RecentAgentDecisions({ agentId }: { agentId: string }) {
  const { t, i18n } = useTranslation('agent')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<AgentApproval[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try {
      const [approved, denied] = await Promise.all([listAgentApprovals(agentId, { status: 'approved' }), listAgentApprovals(agentId, { status: 'denied' })])
      setItems([...approved, ...denied].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 20))
    } catch (failure) { setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => { if (open && items === null) void load() }, [open, items, load])
  async function revoke(item: AgentApproval) {
    if (!await confirmAction(t('revokeConfirm'))) return
    setBusy(true); setError(null); setNotice(null)
    try { await revokeAgentApproval(agentId, item, newId()); setNotice(t('revoked')); await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }
  const time = (value: string) => new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
  return <details className="agent-recent-decisions" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
    <summary>{t('recent')}</summary>
    {error && <ErrorNotice error={error} />}
    <div aria-live="polite">{notice && <p className="form-success" role="status">{notice}</p>}</div>
    {items?.length === 0 && <p className="quiet-note">{t('recentEmpty')}</p>}
    {!!items?.length && <ul className="channel-list">{items.map((item) => {
      const name = t(`tools.${item.tool_name.replace(/\./g, '_')}`, { defaultValue: item.tool_name })
      return <li key={item.id} className="channel-card">
        <p><strong>{name}</strong> · {t(`status_${item.status}`)}{item.decision && <> · {t(`decision_${item.decision}`, { defaultValue: item.decision })}</>} · <time dateTime={item.created_at}>{time(item.created_at)}</time></p>
        <p className="agent-approval-summary"><code>{item.summary}</code></p>
        {item.status === 'approved' && <button type="button" className="text-button" disabled={busy} aria-label={`${t('revoke')}: ${name}`} onClick={() => { void revoke(item) }}>{t('revoke')}</button>}
      </li>
    })}</ul>}
  </details>
}

export function AgentApprovals({ agentId, taskId, onChanged, refreshKey }: { agentId: string; taskId?: string; onChanged?: () => void; refreshKey?: unknown }) {
  const { t, i18n } = useTranslation('agent')
  const [items, setItems] = useState<AgentApproval[]>([])
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [details, setDetails] = useState<Record<string, AgentApproval>>({})
  const pending = useRef<Record<string, string>>({})
  const load = useCallback((signal?: AbortSignal) => listAgentApprovals(agentId, { taskId, status: 'pending' }, signal).then(setItems, (failure: unknown) => { if (!signal?.aborted) setError(asApiError(failure)) }), [agentId, taskId])
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort() }, [load, refreshKey])

  async function decide(item: AgentApproval, decision: AgentDecision) {
    if (busy) return
    const key = `${item.id}:${item.revision}:${decision}`
    // O mesmo UUID é reenviado se a resposta se perder: a decisão é idempotente.
    pending.current[key] ??= newId()
    setBusy(item.id); setError(null)
    try {
      await decideAgentApproval(agentId, item, decision, pending.current[key])
      delete pending.current[key]
      await load(); onChanged?.()
    } catch (failure) { setError(asApiError(failure)); void load() } finally { setBusy(null) }
  }
  async function inspect(item: AgentApproval) {
    if (details[item.id]) { setDetails((current) => Object.fromEntries(Object.entries(current).filter(([key]) => key !== item.id))); return }
    try { const value = await getAgentApproval(agentId, item.id); setDetails((current) => ({ ...current, [item.id]: value })) } catch (failure) { setError(asApiError(failure)) }
  }
  if (!items.length && !error) return null
  return <section className="agent-approvals" aria-label={t('approvalsTitle')}>
    {error && <ErrorNotice error={error} />}
    {items.map((item) => <article key={item.id} className="agent-approval">
      <p className="section-label">{t('approvalNeeded')}</p>
      <h4>{t(`tools.${item.tool_name.replace(/\./g, '_')}`, { defaultValue: item.tool_name })}</h4>
      <p className="agent-approval-summary"><code>{item.summary}</code></p>
      {!taskId && <p className="quiet-note">{item.task_title}</p>}
      <p className="quiet-note">{t('where', { target: t(`target_${item.target.kind}`, { defaultValue: item.target.kind }), effect: t(`effect_${item.effect}`, { defaultValue: item.effect }) })} {item.expires_at && t('expiresAt', { time: new Intl.DateTimeFormat(i18n.resolvedLanguage, { timeStyle: 'short' }).format(new Date(item.expires_at)) })}</p>
      {!item.valid && <p className="transfer-notice" role="alert">{t('approvalStale', { reason: staleText(t, item.stale_reason) })}</p>}
      <button type="button" className="text-button" onClick={() => { void inspect(item) }}>{t(details[item.id] ? 'hideDetails' : 'showDetails')}</button>
      {details[item.id]?.arguments_preview && <pre className="agent-approval-preview">{JSON.stringify(details[item.id].arguments_preview, null, 2)}</pre>}
      <div className="agent-approval-actions" role="group" aria-label={item.summary}>
        <button type="button" className="button primary" disabled={!!busy || !item.valid} onClick={() => { void decide(item, 'allow_once') }}>{t('allowOnce')}</button>
        <button type="button" className="button secondary" disabled={!!busy || !item.valid} onClick={() => { void decide(item, 'allow_tool') }}>{t('allowTool')}</button>
        <button type="button" className="button secondary" disabled={!!busy} onClick={() => { void decide(item, 'deny') }}>{t('deny')}</button>
      </div>
      <small>{t('allowToolHelp')}</small>
      {(BROAD.has(item.target.kind) || BROAD_TOOLS.test(item.tool_name)) && <p className="transfer-notice">{t('allowToolBroad')}</p>}
    </article>)}
  </section>
}
