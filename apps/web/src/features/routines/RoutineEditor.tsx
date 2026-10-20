import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { Routine, RoutineDefinition } from '../../api/routines'
import type { Skill, SkillDefinition } from '../../api/skills'

export interface RoutineEditorState { definition: RoutineDefinition; source: SkillDefinition; previous?: Routine }
function supportsSchedule(source: SkillDefinition) { return source.resources.environment === 'none' && source.resources.tools.length === 0 || source.resources.environment === 'own' && source.resources.tools.length === 1 && source.resources.tools[0] === 'text.normalize' }

export function RoutineEditor({ initial, skills, conversationId, disabled, onReview, onCancel }: {
  initial: RoutineEditorState; skills: Skill[]; conversationId: string | null; disabled: boolean
  onReview: (definition: RoutineDefinition, previous: Routine | undefined, source: SkillDefinition) => void; onCancel: () => void
}) {
  const { t } = useTranslation('routines')
  const [value, setValue] = useState(initial.definition)
  const heading = useRef<HTMLHeadingElement | null>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const [source, setSource] = useState(initial.source)
  const selected = `${value.skill_id}:${value.skill_version}`
  const canSchedule = supportsSchedule(source)
  const pinnedMissing = !skills.some((skill) => `${skill.id}:${skill.current_version}` === selected)
  function chooseSkill(key: string) {
    const skill = skills.find((item) => `${item.id}:${item.current_version}` === key)
    if (!skill) return
    setSource(skill.current.definition)
    setValue({ ...value, skill_id: skill.id, skill_version: skill.current_version, environment: skill.current.definition.resources.environment,
      execution_mode: supportsSchedule(skill.current.definition) ? value.execution_mode ?? 'prepare' : 'prepare',
      inputs: Object.fromEntries(skill.current.definition.inputs.map((input) => [input.name, value.inputs[input.name] ?? ''])) })
  }
  function submit(event: FormEvent) {
    event.preventDefault()
    if (!disabled) onReview({ ...value, inputs: Object.fromEntries(Object.entries(value.inputs).filter(([, input]) => input.trim())) }, initial.previous, source)
  }
  function selectCurrentResource() { const next = { ...value }; delete next.runtime_binding; setValue(next) }
  return <form className="routine-editor" onSubmit={submit}>
    <h3 tabIndex={-1} ref={heading}>{t(initial.previous ? 'edit' : 'create')}</h3><p className="quiet-note">{t('noSecrets')}</p>
    <fieldset disabled={disabled}>
      <label className="field">{t('name')}<input required maxLength={200} value={value.name} onChange={(event) => setValue({ ...value, name: event.target.value })} /></label>
      <label className="field">{t('instructions')}<textarea required maxLength={4096} value={value.instructions} onChange={(event) => setValue({ ...value, instructions: event.target.value })} /></label>
      <label className="field">{t('skill')}<select value={selected} onChange={(event) => chooseSkill(event.target.value)}>
        {pinnedMissing && <option value={selected}>{initial.source.name} · {t('version', { version: value.skill_version })} · {t('fixed')}</option>}
        {skills.map((skill) => <option key={skill.id} value={`${skill.id}:${skill.current_version}`}>{skill.current.definition.name} · {t('version', { version: skill.current_version })}</option>)}
      </select></label>
      <fieldset><legend>{t('inputs')}</legend>{source.inputs.map((input) => <label key={input.name} className="field">{input.description} ({t(input.required ? 'required' : 'optional')})<input required={input.required} maxLength={2048} value={value.inputs[input.name] ?? ''} onChange={(event) => setValue({ ...value, inputs: { ...value.inputs, [input.name]: event.target.value } })} /></label>)}</fieldset>
      <div className="routine-grid">
        <label className="field">{t('frequency')}<select value={value.schedule.frequency} onChange={(event) => setValue({ ...value, schedule: { ...value.schedule, frequency: event.target.value as 'daily' | 'weekly', weekdays: event.target.value === 'weekly' ? [0] : [] } })}><option value="daily">{t('daily')}</option><option value="weekly">{t('weekly')}</option></select></label>
        <label className="field">{t('time')}<input type="time" required value={value.schedule.time} onChange={(event) => setValue({ ...value, schedule: { ...value.schedule, time: event.target.value } })} /></label>
      </div>
      {value.schedule.frequency === 'weekly' && <fieldset className="routine-days"><legend>{t('weekdays')}</legend>{Array.from({ length: 7 }, (_, day) => <label key={day} className="checkbox-label"><input type="checkbox" checked={value.schedule.weekdays.includes(day)} onChange={(event) => setValue({ ...value, schedule: { ...value.schedule, weekdays: event.target.checked ? [...value.schedule.weekdays, day].sort() : value.schedule.weekdays.filter((item) => item !== day) } })} />{t(`day_${day}`)}</label>)}</fieldset>}
      <label className="field">{t('timezone')}<input required maxLength={100} value={value.timezone} onChange={(event) => setValue({ ...value, timezone: event.target.value })} /><small>{t('timezoneHelp')}</small></label>
      <label className="field">{t('grace')}<input type="number" min={0} max={3600} step={1} required value={value.grace_seconds} onChange={(event) => setValue({ ...value, grace_seconds: Number(event.target.value) })} /></label>
      <p>{t('environment')}: <strong>{t(`environment_${value.environment}`)}</strong></p>
      <label className="field">{t('destination')}<select required value={value.destination.conversation_id} onChange={(event) => setValue({ ...value, destination: { kind: 'conversation', conversation_id: event.target.value } })}>
        {conversationId && <option value={conversationId}>{t('currentConversation')}</option>}
        {value.destination.conversation_id !== conversationId && <option value={value.destination.conversation_id}>{t('savedConversation')}</option>}
      </select></label>
      <label className="field">{t('notification')}<select value={value.notification} onChange={(event) => setValue({ ...value, notification: event.target.value as RoutineDefinition['notification'] })}>{(['always', 'failures', 'none'] as const).map((condition) => <option key={condition} value={condition}>{t(`notification_${condition}`)}</option>)}</select></label>
      <label className="checkbox-label"><input type="checkbox" disabled={!canSchedule} checked={value.execution_mode === 'scheduled'} onChange={(event) => setValue({ ...value, execution_mode: event.target.checked ? 'scheduled' : 'prepare' })} /><span>{t('executeScheduled')}</span></label><p className="quiet-note">{t('executeHelp')}</p>
      {value.execution_mode === 'scheduled' && <p className="quiet-note">{t(value.runtime_binding ? 'runtimePinned' : 'runtimeSelect')}</p>}{value.execution_mode === 'scheduled' && value.runtime_binding && <button type="button" className="text-button" onClick={selectCurrentResource}>{t('selectCurrentResource')}</button>}
      <p className="quiet-note">{t('policy')}</p>
      <div className="routine-actions"><button type="submit" className="button primary">{t('review')}</button><button type="button" className="button secondary" onClick={onCancel}>{t('cancelEdit')}</button></div>
    </fieldset>
  </form>
}
