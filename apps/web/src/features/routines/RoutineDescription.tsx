import { useTranslation } from 'react-i18next'
import type { RoutineDefinition } from '../../api/routines'

export function RoutineTime({ value, timezone }: { value: string; timezone: string }) {
  const { i18n } = useTranslation('routines')
  return <time dateTime={value}>{new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short', timeZone: timezone }).format(new Date(value))} ({timezone})</time>
}

export function RoutineDescription({ definition }: { definition: RoutineDefinition }) {
  const { t } = useTranslation('routines')
  return <div className="routine-description">
    <h4>{definition.name}</h4><p>{definition.instructions}</p>
    <dl>
      <dt>{t('skill')}</dt><dd><code>{definition.skill_id}</code> · {t('version', { version: definition.skill_version })}</dd>
      <dt>{t('frequency')}</dt><dd>{t(definition.schedule.frequency)} · {definition.schedule.time} ({definition.timezone}){definition.schedule.frequency === 'weekly' && <p>{definition.schedule.weekdays.map((day) => t(`day_${day}`)).join(', ')}</p>}</dd>
      <dt>{t('grace')}</dt><dd>{definition.grace_seconds}</dd>
      <dt>{t('environment')}</dt><dd>{t(`environment_${definition.environment}`)}</dd>
      <dt>{t('destination')}</dt><dd><code>{definition.destination.conversation_id}</code></dd>
      <dt>{t('notification')}</dt><dd>{t(`notification_${definition.notification}`)}</dd>
      <dt>{t('execution')}</dt><dd>{t(definition.execution_mode === 'scheduled' ? 'scheduledExecution' : 'prepareOnly')}</dd>
      {definition.execution_mode === 'scheduled' && <><dt>{t('runtimeContract')}</dt><dd>{t(definition.runtime_binding ? 'runtimePinned' : 'runtimeSelect')}</dd></>}
    </dl>
    {Object.entries(definition.inputs).length > 0 && <><h4>{t('inputs')}</h4><dl>{Object.entries(definition.inputs).map(([name, input]) => <div key={name}><dt>{name}</dt><dd>{input}</dd></div>)}</dl></>}
    <p className="quiet-note">{t('policy')}</p>
  </div>
}
