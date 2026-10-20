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
