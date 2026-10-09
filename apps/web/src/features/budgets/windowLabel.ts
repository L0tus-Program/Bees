import { useTranslation } from 'react-i18next'

// Janelas inteiras de vários dias aparecem em dias; o restante, em horas.
export function useWindowLabel() {
  const { t } = useTranslation('budgets')
  return (seconds: number) => seconds % 86400 === 0 && seconds >= 86400 * 2
    ? t('days', { count: seconds / 86400 }) : t('hours', { count: Math.round(seconds / 3600) })
}
