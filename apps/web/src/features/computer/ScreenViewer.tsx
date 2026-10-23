import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Computer } from '../../api/computers'
import { viewUrl } from '../../api/computers'

type ViewState = 'connecting' | 'connected' | 'disconnected'

// Tela autenticada pela sessão; entrada só é aceita quando o usuário está no controle.
export function ScreenViewer({ computer }: { computer: Computer }) {
  const { t } = useTranslation('computer')
  const target = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<ViewState>('connecting')
  const [attempt, setAttempt] = useState(0)
  const interactive = computer.control_holder === 'human'
  useEffect(() => {
    let rfb: { disconnect(): void; viewOnly: boolean; scaleViewport: boolean; addEventListener: EventTarget['addEventListener'] } | null = null
    let cancelled = false
    void import('@novnc/novnc').then(({ default: RFB }) => {
      if (cancelled || !target.current) return
      const client = new RFB(target.current, viewUrl(computer), { wsProtocols: ['binary'] })
      client.viewOnly = !interactive
      client.scaleViewport = true
      client.addEventListener('connect', () => setState('connected'))
      client.addEventListener('disconnect', () => setState('disconnected'))
      rfb = client
    }, () => setState('disconnected'))
    return () => { cancelled = true; rfb?.disconnect() }
  }, [computer, interactive, attempt])
  return <div className="screen-viewer">
    <div className="screen-viewer-bar">
      <span role="status">{t(`view_${state}`)} · {t(interactive ? 'viewInteractive' : 'viewReadOnly')}</span>
      {state === 'disconnected' && <button type="button" className="text-button" onClick={() => { setState('connecting'); setAttempt((value) => value + 1) }}>{t('reconnect')}</button>}
    </div>
    <div className="screen-viewer-canvas" ref={target} aria-label={t('screenLabel')} />
  </div>
}
