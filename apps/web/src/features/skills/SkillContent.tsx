import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { Skill, SkillDefinition } from '../../api/skills'

export type Editor = { definition: SkillDefinition; previous?: Skill; imported?: boolean }

export function SkillDescription({ definition }: { definition: SkillDefinition }) {
  const { t } = useTranslation('skills')
  return <div className="skill-description">
    <p>{definition.purpose}</p>
    {definition.inputs.length > 0 && <><h4>{t('inputs')}</h4><ul>{definition.inputs.map((input) => <li key={input.name}><strong>{input.name}</strong>: {input.description} ({t(input.required ? 'required' : 'optional')})</li>)}</ul></>}
    <ol>{definition.steps.map((step, index) => <li key={index}><strong>{step.title}</strong><p>{step.instruction}</p></li>)}</ol>
    <dl><dt>{t('environment')}</dt><dd>{t(`environment_${definition.resources.environment}`)}</dd><dt>{t('tools')}</dt><dd>{definition.resources.tools.join(', ') || t('none')}</dd><dt>{t('output')}</dt><dd>{definition.output}</dd><dt>{t('completion')}</dt><dd>{definition.completion}</dd></dl>
  </div>
}

export function SkillEditor({ initial, disabled, canSave = true, onSave, onCancel }: { initial: Editor; disabled: boolean; canSave?: boolean; onSave: (definition: SkillDefinition) => void; onCancel: () => void }) {
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
      <div className="skill-actions"><button type="submit" className="button primary" disabled={!canSave}>{t(disabled ? 'saving' : 'save')}</button><button type="button" className="button secondary" onClick={onCancel}>{t('cancel')}</button></div>
    </fieldset>
  </form>
}

