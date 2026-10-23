import './features/auth/setupAuthorization'
import React from 'react'
import ReactDOM from 'react-dom/client'
import './i18n'
import { App } from './App'
import './styles.css'
import { applyAppearance, readAppearance } from './components/appearance'

applyAppearance(readAppearance())

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// Instalação como app (PWA) somente no build servido pelo Bees, nunca no Vite de desenvolvimento.
if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
  window.addEventListener('load', () => { void navigator.serviceWorker.register('/sw.js').catch(() => undefined) })
}
