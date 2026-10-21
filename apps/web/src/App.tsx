import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from './api/client'
import type { ApiError } from './api/client'
import { authStatus, logout } from './api/onboarding'
import { ErrorNotice, LoadingNotice } from './components/Feedback'
import { AuthForm } from './features/auth/AuthForm'
import { Dashboard } from './features/dashboard/Dashboard'
import { useResource } from './hooks/useResource'
import { setupAuthorization } from './features/auth/setupAuthorization'
import { AppearanceSelect } from './components/AppearanceSelect'
import { clearRestorePending } from './features/portability/restoreSession'
import { clearExportState } from './features/portability/session'

const ExportPanel = lazy(() => import('./features/portability/ExportPanel'))

export function App() {
  const { t, i18n } = useTranslation('product')
  const { t: dataText } = useTranslation('portability')
  const { resource, reload } = useResource(authStatus)
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [dataOpen, setDataOpen] = useState(false)
  const [dataBusy, setDataBusy] = useState(false)
  const dataButton = useRef<HTMLButtonElement>(null)
  const hadDataOpen = useRef(false)
  useEffect(() => {
    if (dataOpen) hadDataOpen.current = true
    else if (hadDataOpen.current) { hadDataOpen.current = false; dataButton.current?.focus() }
  }, [dataOpen])
  useEffect(() => {
    if (resource.state === 'ready' && resource.data.configured) setupAuthorization.clear()
    if (resource.state === 'ready' && !resource.data.authenticated) {
      try { clearRestorePending() } catch { /* Private tab storage can be unavailable. */ }
      try { clearExportState() } catch { /* Unavailable tab storage is never used as authentication. */ }
    }
  }, [resource])
  useEffect(() => {
    const expired = () => { setError(null); setDataOpen(false); setDataBusy(false); try { clearRestorePending() } catch { /* Private tab storage can be unavailable. */ }
      try { clearExportState() } catch { /* Storage may be unavailable; no server copy is changed. */ } reload() }
    window.addEventListener('bees:session-expired', expired)
    return () => window.removeEventListener('bees:session-expired', expired)
  }, [reload])

  async function signOut() {
    setDataOpen(false); setDataBusy(false)
    try { clearRestorePending() } catch { /* Private tab storage can be unavailable. */ }
    try { clearExportState() } catch { /* Storage may be unavailable. */ }
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
        <div className="header-controls"><span className="release-label">{t('textAssistant')}</span><AppearanceSelect /><label className="language-select"><span className="sr-only">{t('language')}</span><select value={i18n.resolvedLanguage ?? 'pt-BR'} onChange={(event) => { void i18n.changeLanguage(event.target.value) }}><option value="pt-BR">Português</option><option value="en">English</option><option value="es">Español</option></select></label>{authenticated && <button type="button" ref={dataButton} className="text-button" aria-pressed={dataOpen} disabled={signingOut || dataBusy} onClick={() => setDataOpen(value => !value)}>{dataText('navigation')}</button>}{authenticated && <button type="button" className="text-button" disabled={signingOut} onClick={() => { void signOut() }}>{t(signingOut ? 'working' : 'signOut')}</button>}</div>
      </header>
      <main id="main" tabIndex={-1}>
        {error && <ErrorNotice error={error} />}
        {(resource.state === 'loading' || signingOut) && <LoadingNotice />}
        {resource.state === 'error' && <div className="surface resource-error"><h1>{t('serviceUnavailable')}</h1><ErrorNotice error={resource.error} /><button className="button secondary" type="button" onClick={reload}>{t('retry')}</button></div>}
        {resource.state === 'ready' && !signingOut && (resource.data.authenticated
          ? dataOpen ? <Suspense fallback={<LoadingNotice />}><ExportPanel onClose={() => setDataOpen(false)} onBusy={setDataBusy} /></Suspense> : <Dashboard userName={resource.data.user?.name ?? ''} />
          : <AuthForm configured={resource.data.configured} onAuthenticated={reload} />)}
      </main>
      <footer><span>{t('footer')}</span><span className="footer-mark" aria-hidden="true">✳</span></footer>
    </div>
  )
}
