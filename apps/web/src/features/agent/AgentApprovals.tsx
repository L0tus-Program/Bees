import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { decideAgentApproval, getAgentApproval, listAgentApprovals } from '../../api/agent'
import type { AgentApproval, AgentDecision } from '../../api/agent'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { ErrorNotice } from '../../components/Feedback'

function newId() { return crypto.randomUUID() }

export function AgentApprovals({ agentId, taskId, onChanged, refreshKey }: { agentId: string; taskId?: string; onChanged?: () => void; refreshKey?: unknown }) {
  const { t } = useTranslation('agent')
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
      {!item.valid && <p className="transfer-notice">{t('approvalStale', { reason: item.stale_reason })}</p>}
      <button type="button" className="text-button" onClick={() => { void inspect(item) }}>{t(details[item.id] ? 'hideDetails' : 'showDetails')}</button>
      {details[item.id]?.arguments_preview && <pre className="agent-approval-preview">{JSON.stringify(details[item.id].arguments_preview, null, 2)}</pre>}
      <div className="agent-approval-actions">
        <button type="button" className="button primary" disabled={!!busy || !item.valid} onClick={() => { void decide(item, 'allow_once') }}>{t('allowOnce')}</button>
        <button type="button" className="button secondary" disabled={!!busy || !item.valid} onClick={() => { void decide(item, 'allow_tool') }}>{t('allowTool')}</button>
        <button type="button" className="button secondary" disabled={!!busy} onClick={() => { void decide(item, 'deny') }}>{t('deny')}</button>
      </div>
      <small>{t('allowToolHelp')}</small>
    </article>)}
  </section>
}
