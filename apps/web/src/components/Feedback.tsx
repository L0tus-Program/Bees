import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { ApiError } from '../api/client'

export function ErrorNotice({ error }: { error: ApiError }) {
  const { t, i18n } = useTranslation('product')
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => { ref.current?.focus() }, [error])
  const key = i18n.exists(`errors.${error.code}`, { ns: 'product' }) ? `errors.${error.code}` : 'errors.unknown_error'
  return <p className="form-error" role="alert" tabIndex={-1} ref={ref}>{t(key)}</p>
}

export function LoadingNotice() {
  const { t } = useTranslation('product')
  return <p className="loading-notice" role="status">{t('loading')}</p>
}
