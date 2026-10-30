import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { getRoutineProposal, proposalDefinition } from '../../api/routineProposals'
import type { RoutineProposal } from '../../api/routineProposals'
import { definitionIntent, listRoutines, sendRoutine } from '../../api/routines'
import type { RoutineDefinition, RoutineIntent } from '../../api/routines'
import { listSkills } from '../../api/skills'
import type { Skill, SkillDefinition } from '../../api/skills'
import { restoreManualRoutine } from '../../api/routineManual'
import { ErrorNotice } from '../../components/Feedback'
import { RoutineEditor } from './RoutineEditor'
import type { RoutineEditorState } from './RoutineEditor'
import { RoutineDescription } from './RoutineDescription'
import { clearRoutineIntent, preserveRoutineIntent, readRoutineRecovery } from './recovery'
import './routines.css'

export function RoutineProposalDraft({ agentId, conversationId, messageId, onClose, onLock }: { agentId: string; conversationId: string; messageId: string; onClose: () => void; onLock: (locked: boolean) => void }) {
  const { t } = useTranslation('routines')
  const [recovery] = useState(() => readRoutineRecovery(agentId))
  const [manualRecovery] = useState(() => restoreManualRoutine(agentId))
  const ownPending = recovery.intent?.action === 'create' && recovery.intent.body.proposal_source?.message_id === messageId && recovery.intent.body.proposal_source?.conversation_id === conversationId
  const [pending, setPending] = useState<RoutineIntent | null>(ownPending ? recovery.intent : null)
  const blocked = recovery.corrupt || !!recovery.intent && !ownPending || manualRecovery.corrupt || manualRecovery.intent !== null
  const [proposal, setProposal] = useState<RoutineProposal | null>(null)
  const [skills, setSkills] = useState<Skill[]>([])
  const [more, setMore] = useState(false)
  const [draft, setDraft] = useState<RoutineEditorState | null>(null)
  const [review, setReview] = useState<RoutineIntent | null>(null)
  const [saved, setSaved] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ApiError | null>(null)
  const [storageUnavailable, setStorageUnavailable] = useState(false)
  const reading = useRef<AbortController | null>(null)
  const writing = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const heading = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => { onLock(busy || pending !== null) }, [busy, pending, onLock])
  useEffect(() => { heading.current?.focus() }, [review, pending, saved])
  const load = useCallback(async () => {
    reading.current?.abort(); const controller = new AbortController(); reading.current = controller
    setLoading(true); setError(null)
    try {
      const [value, list] = await Promise.all([getRoutineProposal(agentId, conversationId, messageId, controller.signal), listSkills(agentId, 0, controller.signal)])
      if (controller.signal.aborted) return
      setProposal(value); setSkills(list); setMore(list.length === 50)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [agentId, conversationId, messageId])
  useEffect(() => { if (!ownPending && !blocked) void load(); else setLoading(false); return () => { reading.current?.abort(); writing.current?.abort() } }, [load, blocked, ownPending])
  async function moreSkills() {
    if (locked.current) return
    locked.current = true; setBusy(true)
    const controller = new AbortController(); reading.current = controller
    try { const list = await listSkills(agentId, skills.length, controller.signal); if (!controller.signal.aborted) { setSkills((old) => [...old, ...list.filter((item) => !old.some((prior) => prior.id === item.id))]); setMore(list.length === 50) } }
    catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  function prepare(definition: RoutineDefinition, _previous: unknown, source: SkillDefinition) {
    if (!proposal) return
    try { setDraft({ definition, source }); setReview(definitionIntent(agentId, definition, undefined, proposal.source)); setError(null); setConflict(false) }
    catch (failure) { setError(asApiError(failure)) }
  }
  async function send(intent: RoutineIntent, verified = false) {
    if (locked.current) return
    locked.current = true; setBusy(true); setError(null)
    const controller = new AbortController(); writing.current = controller
    let submitted = false
    try {
      if (verified) await listRoutines(agentId, 0, controller.signal)
      if (controller.signal.aborted) return
      setPending(intent); setStorageUnavailable(!preserveRoutineIntent(intent))
      submitted = true
      await sendRoutine(intent, controller.signal)
      if (controller.signal.aborted) return
      clearRoutineIntent(agentId); setPending(null); setReview(null); setDraft(null); setSaved(true)
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asApiError(failure); setError(problem)
      if (submitted && problem.status >= 400 && problem.status < 500 && problem.code !== 'invalid_response') {
        clearRoutineIntent(agentId); setPending(null)
        if (problem.status === 409) { setReview(null); await load(); setConflict(true) }
      }
    } finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  return <section className="surface routine-proposal" aria-labelledby="routine-proposal-heading" aria-busy={busy || loading}>
    <h3 id="routine-proposal-heading" ref={heading} tabIndex={-1}>{t('proposalTitle')}</h3>
    <p>{t('proposalHelp')}</p>
    {error && <ErrorNotice error={error} />}{storageUnavailable && <p role="status">{t('storageUnavailable')}</p>}
    {conflict && <p role="status">{t('conflict')}</p>}
    {blocked ? <p role="alert">{t('proposalOtherPending')}</p> : pending ? <><p role="status">{t('lost')}</p>{pending.body.definition && <RoutineDescription definition={pending.body.definition} />}<button type="button" className="button secondary" disabled={busy} onClick={() => { void send(pending, true) }}>{t('verify')}</button></> : saved ? <p role="status">{t('proposalSaved')}</p> : <>
      {loading && <p role="status">{t('loading')}</p>}
      {proposal && <details><summary>{t('proposalOriginal')}</summary><p>{proposal.proposal.name}</p><p>{proposal.proposal.instructions}</p><p>{proposal.proposal.schedule.frequency} · {proposal.proposal.schedule.time} · {proposal.proposal.timezone}</p></details>}
      {!loading && !proposal && <button type="button" className="button secondary" disabled={busy} onClick={() => { void load() }}>{t('refresh')}</button>}
      {proposal && !draft && <label className="field">{t('proposalSkill')}<select value="" disabled={busy || loading} onChange={(event) => { const skill = skills.find((item) => item.id === event.target.value); if (skill) setDraft({ definition: proposalDefinition(proposal, skill), source: skill.current.definition }) }}><option value="">{t('proposalChooseSkill')}</option>{skills.map((skill) => <option key={skill.id} value={skill.id}>{skill.current.definition.name} · {t('version', { version: skill.current_version })}</option>)}</select></label>}
      {proposal && !loading && skills.length === 0 && <p>{t('noSkills')}</p>}
      {more && <button type="button" className="text-button" disabled={busy || loading} onClick={() => { void moreSkills() }}>{t('moreSkills')}</button>}
      {draft && !review && <><p>{t('proposalInputs')}</p><RoutineEditor key={`${proposal?.source.content_hash ?? 'draft'}:editor`} initial={draft} skills={skills} conversationId={conversationId} disabled={busy || loading} onReview={prepare} onCancel={() => setDraft(null)} /></>}
      {review?.body.definition && <div className="routine-review"><RoutineDescription definition={review.body.definition} /><p>{t('saveEffect')}</p><button type="button" className="button primary" disabled={busy || loading} onClick={() => { void send(review) }}>{t('confirm')}</button><button type="button" className="button secondary" disabled={busy} onClick={() => setReview(null)}>{t('back')}</button></div>}
    </>}
    <button type="button" className="text-button" disabled={busy || pending !== null} onClick={onClose}>{t('proposalClose')}</button>
  </section>
}
