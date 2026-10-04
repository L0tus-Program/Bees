import { useTranslation } from 'react-i18next'
import { useServiceHealth } from './useServiceHealth'

export function ServiceHealth() {
  const { t, i18n } = useTranslation()
  const { state, retry } = useServiceHealth()
  const description = state.status === 'offline' ? state.error : `${state.status}Description`

  return (
    <section className="health-card" aria-labelledby="service-heading">
      <div className="card-heading">
        <span className="service-icon" aria-hidden="true">↔</span>
        <div>
          <h2 id="service-heading">{t('healthTitle')}</h2>
          <p>{t('healthDescription')}</p>
        </div>
      </div>
      <div className={`connection-panel ${state.status}`} role="status" aria-live="polite" aria-atomic="true">
        <div className="connection-title">
          <span className="status-dot" aria-hidden="true" />
          <h3>{t(state.status)}</h3>
        </div>
        <p>{t(description)}</p>
      </div>
      <dl className="service-details">
        <div><dt>{t('endpoint')}</dt><dd><code>/api/v1/health</code></dd></div>
        {state.status === 'online' && (
          <div><dt>{t('version')}</dt><dd>{state.response.version}</dd></div>
        )}
      </dl>
      <div className="health-actions">
        <button className="button" type="button" disabled={state.status === 'loading'} onClick={retry}>
          <span aria-hidden="true">↻</span> {t(state.status === 'online' ? 'recheck' : 'retry')}
        </button>
        {state.status === 'online' && (
          <span className="checked-at">
            {t('checkedAt', { time: new Intl.DateTimeFormat(i18n.resolvedLanguage, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(state.checkedAt) })}
          </span>
        )}
      </div>
      <p className="snapshot-note">{t('snapshot')}</p>
    </section>
  )
}
