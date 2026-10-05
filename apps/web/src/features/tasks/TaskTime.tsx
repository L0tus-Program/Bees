import { useTranslation } from 'react-i18next'

export function TaskTime({ value }: { value: string }) {
  const { i18n } = useTranslation('tasks')
  return <time dateTime={value}>{new Intl.DateTimeFormat(i18n.resolvedLanguage, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))}</time>
}
