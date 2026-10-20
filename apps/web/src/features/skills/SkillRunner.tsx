import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import type { Skill } from '../../api/skills'
import { checkedSkillTaskIntent, clearSkillTask, createSkillTask, executableSkill, preserveSkillTask, restoreSkillTask } from '../../api/skillTasks'
import type { SkillTaskIntent } from '../../api/skillTasks'
import type { TaskRecord } from '../../api/tasks'
import { getToolTaskCapabilities } from '../../api/toolTasks'
import { ErrorNotice } from '../../components/Feedback'
import { TaskDetail } from '../tasks/TaskDetail'
import { SkillDescription } from './SkillContent'

export function SkillRunner({ agent, selected, onClose, onLock }: { agent: AgentSummary; selected: Skill | null; onClose: () => void; onLock: (locked: boolean) => void }) {
  const { t } = useTranslation('skills')
  const [recovery] = useState(() => restoreSkillTask(agent.id))
  const [pending, setPending] = useState<SkillTaskIntent | null>(recovery.intent)
  const [corrupt, setCorrupt] = useState(recovery.corrupt)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [storageError, setStorageError] = useState(false)
  const [created, setCreated] = useState<TaskRecord | null>(null)
  const locked = useRef(false)
  const operation = useRef<AbortController | null>(null)
  useEffect(() => { onLock(pending !== null || corrupt || busy); return () => onLock(false) }, [pending, corrupt, busy, onLock])
  useEffect(() => () => operation.current?.abort(), [])
  async function send(intent: SkillTaskIntent) {
    if (locked.current) return
    try { preserveSkillTask(intent); setStorageError(false) } catch { setStorageError(true); return }
    locked.current = true; setBusy(true); setPending(intent); setError(null)
    const controller = new AbortController(); operation.current = controller
    try {
      const task = await createSkillTask(intent, controller.signal)
      if (controller.signal.aborted) return
      clearSkillTask(agent.id); setPending(null); setCreated(task); onClose()
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  function discard() {
    try { clearSkillTask(agent.id); setPending(null); setCorrupt(false); setError(null); onClose() }
    catch { setStorageError(true) }
  }
  return <>
    {(pending || corrupt || selected) && <section className="skill-review" aria-busy={busy} aria-label={t('runTitle')}>
      <h3>{t('runTitle')}</h3>
      {error && <ErrorNotice error={error} />}{storageError && <p role="alert">{t('processStorage')}</p>}
      {pending || corrupt ? <>
        <p role="status">{t(corrupt ? 'processRecoveryBlocked' : 'runPending')}</p>
        {pending && <><p>{t('version', { version: pending.body.skill_version })}</p><dl>{Object.entries(pending.body.inputs).map(([name, text]) => <div key={name}><dt>{name}</dt><dd>{text}</dd></div>)}</dl><button type="button" className="button primary" disabled={busy} onClick={() => { void send(pending) }}>{t('verify')}</button></>}
        <p className="quiet-note">{t('runDiscardHelp')}</p><button type="button" className="text-button" disabled={busy} onClick={discard}>{t('runDiscard')}</button>
      </> : selected && <SkillRunForm key={`${selected.id}:${selected.current_version}`} agentId={agent.id} skill={selected} onSend={(intent) => { void send(intent) }} onClose={onClose} />}
      <p className="quiet-note">{t('runPrivate')}</p>
    </section>}
    {created && <><p role="status">{t('runCreated')}</p><TaskDetail key={created.id} agent={agent} taskId={created.id} onRefresh={() => {}} onClose={() => setCreated(null)} /></>}
  </>
}

function SkillRunForm({ agentId, skill, onSend, onClose }: { agentId: string; skill: Skill; onSend: (intent: SkillTaskIntent) => void; onClose: () => void }) {
  const { t } = useTranslation('skills')
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [available, setAvailable] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    heading.current?.focus()
    const controller = new AbortController()
    void getToolTaskCapabilities(agentId, controller.signal).then((value) => { if (!controller.signal.aborted) setAvailable(value.available) }, (failure) => { if (!controller.signal.aborted) setError(asApiError(failure)) })
    return () => controller.abort()
  }, [agentId])
  return <form onSubmit={(event) => {
    event.preventDefault()
    if (!available || !executableSkill(skill)) return
    try {
      onSend(checkedSkillTaskIntent({ agentId, body: { client_request_id: crypto.randomUUID(), skill_id: skill.id, skill_version: skill.current_version, inputs: Object.fromEntries(Object.entries(inputs).filter(([, value]) => value.trim().length)), max_calls: 4, max_active_seconds: 120 } }, agentId))
    } catch (failure) { setError(asApiError(failure)) }
  }}>
    <h4 tabIndex={-1} ref={heading}>{skill.current.definition.name} · {t('version', { version: skill.current_version })}</h4><SkillDescription definition={skill.current.definition} />
    <p>{t('runHelp')}</p>{!available && <p role="status">{t('runUnavailable')}</p>}{error && <ErrorNotice error={error} />}
    {skill.current.definition.inputs.map((item) => <label key={item.name} className="field"><span>{item.description} · {t(item.required ? 'required' : 'optional')}</span><textarea required={item.required} maxLength={2048} value={inputs[item.name] ?? ''} onChange={(event) => setInputs((old) => ({ ...old, [item.name]: event.target.value }))} /></label>)}
    <div className="skill-actions"><button type="button" className="text-button" onClick={onClose}>{t('cancel')}</button><button type="submit" className="button primary" disabled={!available || !executableSkill(skill)}>{t('runConfirm')}</button></div>
  </form>
}
