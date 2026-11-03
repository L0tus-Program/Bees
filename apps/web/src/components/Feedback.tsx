import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import type { ApiError } from '../api/client'

export function ErrorNotice({ error }: { error: ApiError }) {
  const { t, i18n } = useTranslation('product')
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => { ref.current?.focus() }, [error])
  const key = i18n.exists(`errors.${error.code}`, { ns: 'product' }) ? `errors.${error.code}` : 'errors.unknown_error'
  return <div role="alert"><p className="form-error" tabIndex={-1} ref={ref}>{t(key)}</p>
    {error.diagnostic && <div className="mt-2 text-sm [overflow-wrap:anywhere]">
      {error.diagnostic.provider_message && <p>{error.diagnostic.provider_message}</p>}
      {error.diagnostic.http_status && <p>HTTP {error.diagnostic.http_status} · {error.code}{error.diagnostic.parameter ? ` · ${error.diagnostic.parameter}` : ''}</p>}
    </div>}
  </div>
}

export function LoadingNotice() {
  const { t } = useTranslation('product')
  return <p className="loading-notice" role="status">{t('loading')}</p>
}
