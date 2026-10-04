import { useTranslation } from 'react-i18next'

const available = [
  { id: 'web', symbol: '◈' },
  { id: 'service', symbol: '↔' },
  { id: 'setup', symbol: '⌘' },
] as const

const upcoming = ['assistant', 'own', 'personal'] as const

export function FoundationScope() {
  const { t } = useTranslation()
  return (
    <>
      <section className="scope-section" aria-labelledby="scope-heading">
        <h2 className="section-label" id="scope-heading">{t('scope')}</h2>
        <div className="scope-grid">
          {available.map(({ id, symbol }) => (
            <article className="scope-item" key={id}>
              <span className="scope-icon" aria-hidden="true">{symbol}</span>
              <h3>{t(`${id}Title`)}</h3>
              <p>{t(`${id}Description`)}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="upcoming-section" aria-labelledby="upcoming-heading">
        <div className="upcoming-heading">
          <p className="section-label">{t('next')}</p>
          <h2 id="upcoming-heading">{t('nextTitle')}</h2>
          <p>{t('nextDescription')}</p>
        </div>
        <div className="upcoming-list">
          {upcoming.map((id, index) => (
            <article className="upcoming-item" key={id}>
              <span className="step-number" aria-hidden="true">0{index + 1}</span>
              <div><h3>{t(`${id}Title`)}</h3><p>{t(`${id}Description`)}</p></div>
              <span className="not-ready">{t('unavailable')}</span>
            </article>
          ))}
        </div>
      </section>
    </>
  )
}
