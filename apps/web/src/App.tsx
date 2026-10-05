import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from './api/client'
import type { ApiError } from './api/client'
import { authStatus, logout } from './api/onboarding'
import { ErrorNotice, LoadingNotice } from './components/Feedback'
import { AuthForm } from './features/auth/AuthForm'
import { Dashboard } from './features/dashboard/Dashboard'
import { useResource } from './hooks/useResource'

export function App() {
  const { t, i18n } = useTranslation('product')
  const { resource, reload } = useResource(authStatus)
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  useEffect(() => {
    const expired = () => { setError(null); reload() }
    window.addEventListener('bees:session-expired', expired)
    return () => window.removeEventListener('bees:session-expired', expired)
  }, [reload])

  async function signOut() {
    setSigningOut(true); setError(null)
    try { await logout() } catch (failure) { setError(asApiError(failure)) }
    finally { setSigningOut(false); reload() }
  }

  const authenticated = resource.state === 'ready' && resource.data.authenticated
  return (
    <div className="app-shell product-shell">
      <a className="skip-link" href="#main">{t('skip')}</a>
      <header className="app-header">
        <div className="brand"><img src="/bee.svg" alt="" width="42" height="42" /><span>bees<span className="brand-period">.</span></span></div>
        <div className="header-controls"><span className="release-label">{t('textAssistant')}</span><label className="language-select"><span className="sr-only">{t('language')}</span><select value={i18n.resolvedLanguage ?? 'pt-BR'} onChange={(event) => { void i18n.changeLanguage(event.target.value) }}><option value="pt-BR">Português</option><option value="en">English</option><option value="es">Español</option></select></label>{authenticated && <button type="button" className="text-button" disabled={signingOut} onClick={() => { void signOut() }}>{t(signingOut ? 'working' : 'signOut')}</button>}</div>
      </header>
      <main id="main" tabIndex={-1}>
        {error && <ErrorNotice error={error} />}
        {(resource.state === 'loading' || signingOut) && <LoadingNotice />}
        {resource.state === 'error' && <div className="surface resource-error"><h1>{t('serviceUnavailable')}</h1><ErrorNotice error={resource.error} /><button className="button secondary" type="button" onClick={reload}>{t('retry')}</button></div>}
        {resource.state === 'ready' && !signingOut && (resource.data.authenticated
          ? <Dashboard userName={resource.data.user?.name ?? ''} />
          : <AuthForm configured={resource.data.configured} onAuthenticated={reload} />)}
      </main>
      <footer><span>{t('footer')}</span><span className="footer-mark" aria-hidden="true">✳</span></footer>
    </div>
  )
}
