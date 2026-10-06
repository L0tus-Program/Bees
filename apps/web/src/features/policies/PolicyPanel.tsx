import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { getPolicies, updatePolicy } from '../../api/policies'
import type { PolicyCollection, PolicyRecord } from '../../api/policies'
import { ErrorNotice } from '../../components/Feedback'
import { PolicyForm } from './PolicyForm'
import { editableGenerationPolicy, mergePolicyPage, revocablePolicy } from './state'

export function PolicyPanel({ agent }: { agent: AgentSummary }) {
  const { t, i18n } = useTranslation('policies')
  const [data, setData] = useState<PolicyCollection | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [editor, setEditor] = useState<PolicyRecord | 'new' | null>(null)
  const [revoking, setRevoking] = useState<PolicyRecord | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [offset, setOffset] = useState(0)
  const read = useRef<AbortController | null>(null)
  const mutation = useRef<AbortController | null>(null)
  const reconcileEditor = useRef<string | null>(null)
  useEffect(() => {
    const controller = new AbortController(); read.current = controller
    async function load() {
      let collection = await getPolicies(agent.id, controller.signal, offset)
      const target = reconcileEditor.current
      while (target && !collection.policies.some((policy) => policy.id === target) && collection.next_offset !== null) {
        if (controller.signal.aborted) return null
        collection = mergePolicyPage(collection, await getPolicies(agent.id, controller.signal, collection.next_offset))
      }
      return collection
    }
    void load().then((collection) => {
      if (!collection) return
      if (controller.signal.aborted) return
      setData((previous) => offset === 0 ? collection : mergePolicyPage(previous, collection)); setError(null); setLoading(false)
      if (reconcileEditor.current) {
        const policy = collection.policies.find((item) => item.id === reconcileEditor.current)
        setEditor(policy && editableGenerationPolicy(policy, agent.id) && policy.status === 'active' ? policy : null)
        reconcileEditor.current = null; setNotice('reloaded')
      }
    }, (failure: unknown) => { if (!controller.signal.aborted) { setError(asApiError(failure)); setLoading(false) } })
    return () => controller.abort()
  }, [agent.id, reload, offset])
  useEffect(() => () => mutation.current?.abort(), [])
  const refresh = () => { read.current?.abort(); setLoading(true); setOffset(0); setReload((value) => value + 1) }
  const loadMore = () => {
    if (loading || busy || !data?.next_offset) return
    read.current?.abort(); setLoading(true)
    if (error && offset > 0) setReload((value) => value + 1)
    else setOffset(data.next_offset)
  }
  async function revoke(policy: PolicyRecord) {
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setError(null)
    try {
      await updatePolicy(agent.id, policy.id, { expected_revision: policy.revision, status: 'revoked' }, controller.signal)
      if (!controller.signal.aborted) { setRevoking(null); setNotice('revoked'); refresh() }
    } catch (failure) { if (!controller.signal.aborted) { const apiError = asApiError(failure); setError(apiError); if (apiError.status === 409) { setRevoking(null); setNotice('revokeConflict'); refresh() } } }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <section className="surface policy-panel" aria-labelledby="policy-heading" aria-busy={loading || busy}>
    <div className="form-heading"><div><h2 id="policy-heading">{t('title')}</h2></div><button className="button primary" type="button" disabled={loading || busy || !!error || !!editor || !!revoking} onClick={() => { setEditor('new'); setNotice(null) }}>{t('newRule')}</button></div>
    <p className="form-introduction">{t('description')}</p><p className="transfer-notice">{t('defaults')}</p><p className="quiet-note">{t('changes')}</p>
    {notice && <p className={notice === 'revokeConflict' ? 'form-error' : 'form-success'} role="status">{t(notice)}</p>}
    {loading && <p className="loading-notice" role="status">{t('loading')}</p>}
    {error && <><ErrorNotice error={error} />{data && <p className="quiet-note">{t('stale')}</p>}</>}
    {!editor && <button className="text-button" type="button" disabled={loading || busy} onClick={refresh}>{t('refresh')}</button>}
    {editor && <PolicyForm key={editor === 'new' ? 'new' : `${editor.id}:${editor.revision}`} agent={agent} policy={editor === 'new' ? null : editor} onSaved={() => { setEditor(null); setNotice('saved'); refresh() }} onCancel={() => setEditor(null)} onReload={() => { if (editor !== 'new') reconcileEditor.current = editor.id; refresh() }} />}
    {data?.has_more && <p className="quiet-note">{t('more')}</p>}
    {data && data.policies.length === 0 && !editor && <div className="policy-empty"><h3>{t('empty')}</h3><p>{t('emptyHelp')}</p></div>}
    <div className="policy-list">{data?.policies.map((policy) => {
      const editable = editableGenerationPolicy(policy, agent.id)
      return <article className={`policy-card ${policy.status === 'revoked' ? 'revoked' : ''}`} key={policy.id}>
        <div className="policy-card-heading"><h3>{policy.name}</h3><span className={`policy-effect effect-${policy.effect}`}>{t(policy.effect)}</span></div>
        <p className="quiet-note">{t(policy.status === 'active' ? 'active' : 'revokedStatus')} · {new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(policy.updated_at))}</p>
        {policy.agent_id === null && <p className="quiet-note">{t('global')}</p>}{policy.agent_id !== null && !editable && <p className="quiet-note">{t('unsupported')}</p>}
        {editable && <div className="policy-target">{policy.scope.resource && <p><strong>{t('connection')}</strong><code>{policy.scope.resource}</code></p>}{typeof policy.scope.parameters?.model === 'string' && <p><strong>{t('model')}</strong><span>{policy.scope.parameters.model}</span></p>}{!policy.scope.resource && !policy.scope.parameters?.model && <p>{t('allGeneration')}</p>}</div>}
        {policy.reason && <p className="policy-reason">{policy.reason}</p>}
        {revocablePolicy(policy, agent.id) && <div className="policy-actions">{editable && <button className="text-button" type="button" disabled={loading || busy || !!error || !!editor || !!revoking} onClick={() => { setEditor(policy); setNotice(null) }}>{t('editRule')}</button>}<button className="text-button destructive-text" type="button" disabled={loading || busy || !!error || !!editor || !!revoking} onClick={() => { setRevoking(policy); setNotice(null) }}>{t('revokeRule')}</button></div>}
        {revoking?.id === policy.id && <div className="policy-revoke"><h4>{t('revokeConfirm')}</h4><p>{t('revokeImpact')}</p><div className="policy-actions"><button className="button secondary" type="button" disabled={busy} onClick={() => setRevoking(null)}>{t('cancel')}</button><button className="button danger" type="button" disabled={busy} onClick={() => { void revoke(policy) }}>{t(busy ? 'saving' : 'confirmRevoke')}</button></div></div>}
      </article>
    })}</div>
    {data?.has_more && <button type="button" className="button secondary" disabled={loading || busy || !!editor || !!revoking} onClick={loadMore}>{t(error && offset > 0 ? 'retryPage' : 'loadMore')}</button>}
  </section>
}
