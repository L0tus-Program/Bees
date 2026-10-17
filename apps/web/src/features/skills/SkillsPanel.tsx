import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import type { AgentSummary } from '../../api/onboarding'
import { checkedDocument, enableIntent, exportSkill, importIntent, listSkills, sendSkill, skillIntent, skillRevisions } from '../../api/skills'
import type { Skill, SkillDefinition, SkillIntent, SkillRevision } from '../../api/skills'
import { ErrorNotice } from '../../components/Feedback'
import { TaskTime } from '../tasks/TaskTime'
import './skills.css'

const empty = (): SkillDefinition => ({ name: '', purpose: '', inputs: [], steps: [{ title: '', instruction: '' }], resources: { environment: 'none', tools: [] }, output: '', completion: '' })
type Editor = { definition: SkillDefinition; previous?: Skill; imported?: boolean }

export function SkillDescription({ definition }: { definition: SkillDefinition }) {
  const { t } = useTranslation('skills')
  return <div className="skill-description">
    <p>{definition.purpose}</p>
    {definition.inputs.length > 0 && <><h4>{t('inputs')}</h4><ul>{definition.inputs.map((input) => <li key={input.name}><strong>{input.name}</strong>: {input.description} ({t(input.required ? 'required' : 'optional')})</li>)}</ul></>}
    <ol>{definition.steps.map((step, index) => <li key={index}><strong>{step.title}</strong><p>{step.instruction}</p></li>)}</ol>
    <dl><dt>{t('environment')}</dt><dd>{t(`environment_${definition.resources.environment}`)}</dd><dt>{t('tools')}</dt><dd>{definition.resources.tools.join(', ') || t('none')}</dd><dt>{t('output')}</dt><dd>{definition.output}</dd><dt>{t('completion')}</dt><dd>{definition.completion}</dd></dl>
  </div>
}

export function SkillEditor({ initial, disabled, onSave, onCancel }: { initial: Editor; disabled: boolean; onSave: (definition: SkillDefinition) => void; onCancel: () => void }) {
  const { t } = useTranslation('skills')
  const [value, setValue] = useState(initial.definition)
  const [toolsText, setToolsText] = useState(initial.definition.resources.tools.join(', '))
  function submit(event: FormEvent) { event.preventDefault(); if (!disabled) onSave(value) }
  const field = (name: 'name' | 'purpose' | 'output' | 'completion') => <label className="field">{t(name)}{name === 'name' ? <input required maxLength={100} value={value[name]} onChange={(event) => setValue({ ...value, [name]: event.target.value })} /> : <textarea required maxLength={2048} value={value[name]} onChange={(event) => setValue({ ...value, [name]: event.target.value })} />}</label>
  return <form className="skill-editor" onSubmit={submit}>
    <h3>{t(initial.previous ? 'edit' : initial.imported ? 'reviewImport' : 'create')}</h3>
    <p>{t('saveHelp')}</p>
    <fieldset disabled={disabled}>
      {field('name')}{field('purpose')}
      <h4>{t('inputs')}</h4>
      {value.inputs.map((input, index) => <fieldset key={index}><legend>{t('inputNumber', { number: index + 1 })}</legend>
        <label className="field">{t('inputName')}<input required pattern={'[a-z][a-z0-9_.\\-]{0,63}'} maxLength={64} value={input.name} onChange={(event) => setValue({ ...value, inputs: value.inputs.map((item, i) => i === index ? { ...item, name: event.target.value } : item) })} /></label>
        <label className="field">{t('inputDescription')}<input required maxLength={2048} value={input.description} onChange={(event) => setValue({ ...value, inputs: value.inputs.map((item, i) => i === index ? { ...item, description: event.target.value } : item) })} /></label>
        <label className="checkbox-label"><input type="checkbox" checked={input.required} onChange={(event) => setValue({ ...value, inputs: value.inputs.map((item, i) => i === index ? { ...item, required: event.target.checked } : item) })} />{t('required')}</label>
        <button type="button" className="text-button" onClick={() => setValue({ ...value, inputs: value.inputs.filter((_, i) => i !== index) })}>{t('remove')}</button>
      </fieldset>)}
      {value.inputs.length < 20 && <button type="button" className="button secondary" onClick={() => setValue({ ...value, inputs: [...value.inputs, { name: '', description: '', required: true }] })}>{t('addInput')}</button>}
      <h4>{t('steps')}</h4>
      {value.steps.map((step, index) => <fieldset key={index}><legend>{t('stepNumber', { number: index + 1 })}</legend>
        <label className="field">{t('stepTitle')}<input required maxLength={100} value={step.title} onChange={(event) => setValue({ ...value, steps: value.steps.map((item, i) => i === index ? { ...item, title: event.target.value } : item) })} /></label>
        <label className="field">{t('instruction')}<textarea required maxLength={2048} value={step.instruction} onChange={(event) => setValue({ ...value, steps: value.steps.map((item, i) => i === index ? { ...item, instruction: event.target.value } : item) })} /></label>
        {value.steps.length > 1 && <button type="button" className="text-button" onClick={() => setValue({ ...value, steps: value.steps.filter((_, i) => i !== index) })}>{t('remove')}</button>}
      </fieldset>)}
      {value.steps.length < 30 && <button type="button" className="button secondary" onClick={() => setValue({ ...value, steps: [...value.steps, { title: '', instruction: '' }] })}>{t('addStep')}</button>}
      <label className="field">{t('environment')}<select value={value.resources.environment} onChange={(event) => setValue({ ...value, resources: { ...value.resources, environment: event.target.value as SkillDefinition['resources']['environment'] } })}>{(['none', 'own', 'personal'] as const).map((environment) => <option key={environment} value={environment}>{t(`environment_${environment}`)}</option>)}</select></label>
      <label className="field">{t('tools')}<input maxLength={1300} value={toolsText} onChange={(event) => { setToolsText(event.target.value); setValue({ ...value, resources: { ...value.resources, tools: event.target.value.split(',').map((tool) => tool.trim()).filter(Boolean) } }) }} /><small>{t('toolsHelp')}</small></label>
      {field('output')}{field('completion')}
      <div className="skill-actions"><button type="submit" className="button primary">{t(disabled ? 'saving' : 'save')}</button><button type="button" className="button secondary" onClick={onCancel}>{t('cancel')}</button></div>
    </fieldset>
  </form>
}

export function SkillsPanel({ agent }: { agent: AgentSummary }) {
  const { t } = useTranslation('skills')
  const [items, setItems] = useState<Skill[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [pending, setPending] = useState<SkillIntent | null>(null)
  const [review, setReview] = useState<Skill | null>(null)
  const [history, setHistory] = useState<{ skill: Skill; items: SkillRevision[]; more: boolean } | null>(null)
  const reading = useRef<AbortController | null>(null)
  const writing = useRef<AbortController | null>(null)
  const locked = useRef(false)
  const importGeneration = useRef(Symbol())
  const load = useCallback(async (offset = 0) => {
    reading.current?.abort()
    const controller = new AbortController(); reading.current = controller
    setLoading(true)
    try {
      const page = await listSkills(agent.id, offset, controller.signal)
      if (controller.signal.aborted) return
      setItems((old) => offset ? [...old, ...page.filter((item) => !old.some((previous) => previous.id === item.id))] : page)
      setMore(page.length === 50); setError(null)
    } catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) setLoading(false) }
  }, [agent.id])
  useEffect(() => { void load(); return () => { reading.current?.abort(); writing.current?.abort(); importGeneration.current = Symbol() } }, [load])

  async function send(intent: SkillIntent) {
    if (locked.current) return
    locked.current = true
    reading.current?.abort()
    const controller = new AbortController(); writing.current = controller
    setBusy(true); setLoading(false); setPending(intent); setError(null); setNotice(null)
    try {
      await sendSkill(intent, controller.signal)
      if (controller.signal.aborted) return
      setPending(null); setEditor(null); setReview(null); setHistory(null); setNotice('saved'); await load()
    } catch (failure) {
      if (controller.signal.aborted) return
      const problem = asApiError(failure)
      setError(problem)
      if (problem.status >= 400 && problem.status < 500) {
        setPending(null)
        if (problem.status === 409) { setEditor(null); setReview(null); setNotice('conflict'); await load() }
      }
    } finally { if (!controller.signal.aborted) { locked.current = false; setBusy(false) } }
  }
  function save(definition: SkillDefinition) {
    try { void send(editor?.imported ? importIntent(agent.id, { format: 'bees.skill/v1', scope: 'agent', definition }) : skillIntent(agent.id, definition, editor?.previous)) }
    catch (failure) { setError(asApiError(failure)) }
  }
  async function importFile(file: File) {
    const generation = Symbol(); importGeneration.current = generation
    try {
      if (file.size > 32768) throw new ApiError('invalid_request')
      const document = checkedDocument(JSON.parse(await file.text()))
      if (generation !== importGeneration.current) return
      setEditor({ definition: document.definition, imported: true }); setReview(null); setHistory(null); setError(null)
    } catch { if (generation === importGeneration.current) setError(new ApiError('invalid_request')) }
  }
  async function download(skill: Skill, version: number) {
    setBusy(true)
    try {
      const document = await exportSkill(agent.id, skill.id, version)
      const url = URL.createObjectURL(new Blob([JSON.stringify(document, null, 2)], { type: 'application/json' }))
      const link = documentLink(url, skill.id, version)
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) { setError(asApiError(failure)) }
    finally { setBusy(false) }
  }
  async function showHistory(skill: Skill, offset = 0) {
    setBusy(true)
    try {
      const page = await skillRevisions(agent.id, skill.id, offset)
      setHistory((old) => ({ skill, items: offset && old?.skill.id === skill.id ? [...old.items, ...page] : page, more: page.length === 50 }))
    } catch (failure) { setError(asApiError(failure)) }
    finally { setBusy(false) }
  }
  const disabled = loading || busy || pending !== null
  return <section className="surface skills-panel" aria-labelledby="skills-heading" aria-busy={loading || busy}>
    <div className="form-heading"><h2 id="skills-heading">{t('title')}</h2><button type="button" className="text-button" disabled={busy || loading} onClick={() => { void load() }}>{t('refresh')}</button></div>
    <p>{t('description')}</p><p className="quiet-note">{t('executionScope')}</p>
    {loading && <p role="status">{t('loading')}</p>}
    {error && <ErrorNotice error={error} />}
    {notice && <p role="status">{t(notice)}</p>}
    {pending && !busy && <div className="transfer-notice" role="status"><p>{t('lost')}</p><button type="button" className="button secondary" onClick={() => { void send(pending) }}>{t('verify')}</button></div>}
    <div className="skill-actions"><button type="button" className="button secondary" disabled={disabled || !!error} onClick={() => { importGeneration.current = Symbol(); setEditor({ definition: empty() }); setReview(null); setHistory(null) }}>{t('create')}</button><label className="field">{t('import')}<input type="file" accept="application/json,.json" disabled={disabled} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) void importFile(file) }} /></label></div>
    {editor && <SkillEditor key={JSON.stringify(editor.definition)} initial={editor} disabled={disabled} onSave={save} onCancel={() => { setEditor(null); setError(null) }} />}
    {review && <section className="skill-review"><h3>{t('reviewEnable', { version: review.current_version })}</h3><h4>{review.current.definition.name}</h4><SkillDescription definition={review.current.definition} /><p>{t('enableHelp')}</p><div className="skill-actions"><button type="button" className="button primary" disabled={disabled} onClick={() => { void send(enableIntent(review)) }}>{t('confirmEnable')}</button><button type="button" className="button secondary" disabled={disabled} onClick={() => setReview(null)}>{t('cancel')}</button></div></section>}
    {!loading && !error && items.length === 0 && <p>{t('empty')}</p>}
    <ul className="skill-list">{items.map((skill) => <li key={skill.id}><h3>{skill.current.definition.name}</h3><p>{t('version', { version: skill.current_version })} · {t(skill.enabled_version === null ? 'disabled' : 'enabled')}</p><p>{skill.current.definition.purpose}</p><div className="skill-actions">
      <button type="button" className="button secondary" disabled={disabled || !!error} onClick={() => { importGeneration.current = Symbol(); setEditor({ definition: skill.current.definition, previous: skill }); setHistory(null); setReview(null) }}>{t('edit')}</button>
      <button type="button" className="button secondary" disabled={disabled || !!error} onClick={() => { if (skill.enabled_version === null) { setReview(skill); setEditor(null); setHistory(null) } else void send(enableIntent(skill)) }}>{t(skill.enabled_version === null ? 'enable' : 'disable')}</button>
      <button type="button" className="text-button" disabled={disabled} onClick={() => { void showHistory(skill) }}>{t('history')}</button><button type="button" className="text-button" disabled={disabled} onClick={() => { void download(skill, skill.current_version) }}>{t('export')}</button>
    </div></li>)}</ul>
    {more && <button type="button" className="button secondary" disabled={disabled} onClick={() => { void load(items.length) }}>{t('more')}</button>}
    {history && <section className="skill-history"><h3>{history.skill.current.definition.name} · {t('history')}</h3>{history.items.map((revision) => <details key={revision.version}><summary>{t('version', { version: revision.version })} · {t(`origin_${revision.origin}`)} · <TaskTime value={revision.created_at} /></summary><SkillDescription definition={revision.definition} /><button type="button" className="text-button" disabled={disabled} onClick={() => { void download(history.skill, revision.version) }}>{t('export')}</button></details>)}{history.more && <button type="button" className="button secondary" disabled={disabled} onClick={() => { void showHistory(history.skill, history.items.length) }}>{t('more')}</button>}</section>}
  </section>
}

function documentLink(url: string, skillId: string, version: number) {
  const link = document.createElement('a')
  link.href = url; link.download = `bees-skill-${skillId}-v${version}.json`
  return link
}
