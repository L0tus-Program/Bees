import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { EnvironmentPlan, EnvironmentTemplate, Resources } from '../../api/environments'
import { resourceProfile, validPlan } from './state'

export function EnvironmentForm({ template, agentName, disabled, busy, onSave }: { template: EnvironmentTemplate; agentName: string; disabled: boolean; busy: boolean; onSave: (plan: EnvironmentPlan) => void }) {
  const { t } = useTranslation('environments')
  const [name, setName] = useState(() => t('defaultName', { name: agentName }))
  const [profile, setProfile] = useState('standard')
  const [resources, setResources] = useState<Resources>(() => resourceProfile(template, false))
  const plan = { name: name.trim(), template_id: template.id, ...resources }
  return <form className="environment-form" onSubmit={(event) => { event.preventDefault(); if (!disabled && validPlan(plan, template)) onSave(plan) }}>
    <h3>{t('newPlan')}</h3>
    <fieldset disabled={disabled}><div className="field"><label htmlFor="computer-name">{t('name')}</label><input id="computer-name" required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" /></div>
      <p className="environment-template"><strong>{t('template')}</strong><span>{template.id === 'linux-desktop-v1' ? t('template_linux') : template.name}</span></p>
      {template.id === 'linux-desktop-v1' && <p className="quiet-note">{t('applications')}</p>}
      <div className="field"><label htmlFor="computer-profile">{t('profile')}</label><select id="computer-profile" value={profile} onChange={(event) => { setProfile(event.target.value); if (event.target.value !== 'custom') setResources(resourceProfile(template, event.target.value === 'expanded')) }}><option value="standard">{t('standard')}</option><option value="expanded">{t('expanded')}</option><option value="custom">{t('custom')}</option></select></div>
      <div className="environment-resources">{(['cpu_count', 'memory_mib', 'disk_gib'] as const).map((key) => <div className="field" key={key}><label htmlFor={`computer-${key}`}>{t(key === 'cpu_count' ? 'cpu' : key === 'memory_mib' ? 'memory' : 'disk')}</label><input id={`computer-${key}`} type="number" required step={1} min={template.limits[key].min} max={template.limits[key].max} value={Number.isNaN(resources[key]) ? '' : resources[key]} aria-describedby={`computer-${key}-help`} onChange={(event) => { setResources((previous) => ({ ...previous, [key]: event.target.valueAsNumber })); setProfile('custom') }} /><small id={`computer-${key}-help`}>{t('limits', template.limits[key])}</small></div>)}</div>
      <p className="quiet-note">{t('resourcesNote')}</p><button className="button primary" type="submit" disabled={disabled || !validPlan(plan, template)}>{t(busy ? 'saving' : 'save')}</button>
    </fieldset>
  </form>
}
