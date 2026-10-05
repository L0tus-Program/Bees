import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError, asApiError } from '../../api/client'
import { authenticate } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'
import { setupAuthorization, validSetupToken } from './setupAuthorization'

export function AuthForm({ configured, onAuthenticated }: { configured: boolean; onAuthenticated: () => void }) {
  const { t } = useTranslation('product')
  const [name, setName] = useState('')
  const [bootstrap, setBootstrap] = useState('')
  const [automaticAuthorization, setAutomaticAuthorization] = useState(() => !configured && setupAuthorization.available())
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  const canSetup = automaticAuthorization || validSetupToken(bootstrap.trim())

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    if (!configured && password !== confirmation) { setError(new ApiError('password_mismatch')); return }
    const setupToken = setupAuthorization.forSetup(configured, bootstrap)
    if (!configured && !setupToken) { setError(new ApiError('bootstrap_missing')); return }
    setBusy(true); setError(null)
    controller.current = new AbortController()
    try {
      await authenticate(configured ? { password } : { bootstrap_token: setupToken!, name: name.trim(), password }, controller.current.signal)
      setupAuthorization.clear(); setAutomaticAuthorization(false)
      setPassword(''); setConfirmation(''); setBootstrap('')
      onAuthenticated()
    } catch (failure) {
      if (!controller.current.signal.aborted) {
        const apiError = asApiError(failure)
        if (['bootstrap_invalid', 'already_configured'].includes(apiError.code)) {
          setupAuthorization.clear(); setAutomaticAuthorization(false); setBootstrap('')
        }
        setError(apiError)
      }
    } finally { setBusy(false) }
  }

  return (
    <section className="access-layout">
      <div className="access-copy">
        <p className="eyebrow">{t('welcomeEyebrow')}</p>
        <h1>{t('welcomeTitle')}</h1>
        <p className="introduction">{t('welcomeDescription')}</p>
        <div className="access-benefit"><img src="/bee.svg" width="48" height="48" alt="" /><p>{t('ownership')}</p></div>
        <p className="quiet-note">{t('releaseScope')}</p>
      </div>
      <form className="surface auth-form" onSubmit={(event) => { void submit(event) }}>
        <p className="section-label">{t(configured ? 'welcomeBack' : 'firstAccess')}</p>
        <h2>{t(configured ? 'loginTitle' : 'setupTitle')}</h2>
        <p className="form-introduction">{t(configured ? 'loginDescription' : 'bootstrapDescription')}</p>
        {error && <><ErrorNotice error={error} />{['mutation_timeout', 'mutation_network', 'already_configured'].includes(error.code) && <button type="button" className="text-button" onClick={onAuthenticated}>{t('refreshState')}</button>}</>}
        <fieldset disabled={busy}>
          {!configured && <>
            {automaticAuthorization ? <p className="form-success setup-authorization-note" role="status">{t('setupAuthorized')}</p> : <>
              <p className="transfer-notice">{t('openViaLauncher')}</p>
              <details className="advanced-setup"><summary>{t('advancedSetup')}</summary><label className="field" htmlFor="bootstrap"><span>{t('bootstrapLabel')}</span><input id="bootstrap" type="password" autoComplete="off" value={bootstrap} onChange={(event) => setBootstrap(event.target.value)} minLength={43} maxLength={43} aria-describedby="bootstrap-help" /><small id="bootstrap-help">{t('bootstrapHelp')}</small></label></details>
            </>}
            <label className="field" htmlFor="user-name"><span>{t('yourName')}</span><input id="user-name" autoComplete="name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={80} /></label>
          </>}
          <label className="field" htmlFor="password"><span>{t('password')}</span><input id="password" type="password" autoComplete={configured ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} required minLength={configured ? undefined : 12} maxLength={256} aria-describedby={!configured ? 'password-help' : undefined} />{!configured && <small id="password-help">{t('passwordHelp')}</small>}</label>
          {!configured && <label className="field" htmlFor="confirm-password"><span>{t('confirmPassword')}</span><input id="confirm-password" type="password" autoComplete="new-password" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required maxLength={256} /></label>}
          <button className="button primary wide" type="submit" disabled={!configured && !canSetup}>{t(busy ? 'working' : configured ? 'signIn' : 'createAccount')}</button>
        </fieldset>
        <p className="quiet-note">{t('sessionPrivacy')}</p>
      </form>
    </section>
  )
}
