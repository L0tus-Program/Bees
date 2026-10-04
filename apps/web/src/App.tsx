import { useTranslation } from 'react-i18next'
import { FoundationScope } from './components/FoundationScope'
import { ServiceHealth } from './features/health/ServiceHealth'

export function App() {
  const { t, i18n } = useTranslation()
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">{t('skip')}</a>
      <header className="app-header">
        <div className="brand"><img src="/bee.svg" alt="" width="42" height="42" /><span>bees<span className="brand-period">.</span></span></div>
        <div className="header-controls">
          <span className="foundation-label">{t('foundation')} <span>0.1.0</span></span>
          <label className="language-select">
            <span className="sr-only">{t('language')}</span>
            <select value={i18n.resolvedLanguage ?? 'pt-BR'} onChange={(event) => { void i18n.changeLanguage(event.target.value) }}>
              <option value="pt-BR">Português</option>
              <option value="en">English</option>
              <option value="es">Español</option>
            </select>
          </label>
        </div>
      </header>
      <main id="main" tabIndex={-1}>
        <section className="hero" aria-labelledby="hero-heading">
          <div className="hero-copy">
            <p className="eyebrow"><span aria-hidden="true" />{t('eyebrow')}</p>
            <h1 id="hero-heading">{t('title')}</h1>
            <p className="introduction">{t('introduction')}</p>
          </div>
          <div className="hive-illustration" aria-hidden="true">
            <div className="hive-cell cell-one" /><div className="hive-cell cell-two" /><div className="hive-cell cell-three" />
            <div className="hive-cell cell-four"><img src="/bee.svg" alt="" /></div>
            <div className="hive-cell cell-five" /><div className="hive-cell cell-six" /><div className="hive-cell cell-seven" />
            <span className="hive-caption">BEES / FOUNDATION</span>
          </div>
        </section>
        <div className="foundation-grid">
          <section className="phase-card" aria-labelledby="phase-heading">
            <p className="section-label">{t('phase')}</p>
            <span className="phase-index" aria-hidden="true">01<span>/</span></span>
            <h2 id="phase-heading">{t('phaseTitle')}</h2>
            <p>{t('phaseDescription')}</p>
          </section>
          <ServiceHealth />
        </div>
        <FoundationScope />
      </main>
      <footer><span>{t('footer')}</span><span className="footer-mark" aria-hidden="true">✳</span></footer>
    </div>
  )
}
