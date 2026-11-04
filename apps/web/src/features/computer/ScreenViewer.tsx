import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Computer } from '../../api/computers'
import { viewUrl } from '../../api/computers'

type ViewState = 'connecting' | 'connected' | 'disconnected'

// Tela autenticada pela sessão; entrada só é aceita quando o usuário está no controle.
export function ScreenViewer({ computer }: { computer: Computer }) {
  // Uma identidade/modo novo começa com seu próprio estado de conexão.
  return <ScreenConnection key={JSON.stringify([computer.agent_id, computer.id, computer.control_holder])} computer={computer} />
}

function ScreenConnection({ computer }: { computer: Computer }) {
  const { t } = useTranslation('computer')
  const hintId = useId()
  const target = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<ViewState>('connecting')
  const [attempt, setAttempt] = useState(0)
  const interactive = computer.control_holder === 'human'
  useEffect(() => {
    let rfb: { disconnect(): void; viewOnly: boolean; scaleViewport: boolean; addEventListener: EventTarget['addEventListener'] } | null = null
    let cancelled = false
    function stop() {
      cancelled = true
      const client = rfb; rfb = null
      // Cliente parcialmente inicializado pode falhar também ao desconectar.
      try { client?.disconnect() } catch { /* A conexão já foi invalidada. */ }
    }
    void import('@novnc/novnc').then(({ default: RFB }) => {
      if (cancelled || !target.current) return
      const client = new RFB(target.current, viewUrl(computer), { wsProtocols: ['binary'] })
      rfb = client
      client.viewOnly = !interactive
      client.scaleViewport = true
      client.addEventListener('connect', () => { if (!cancelled) setState('connected') })
      client.addEventListener('disconnect', () => { if (!cancelled) setState('disconnected') })
    }).catch(() => {
      if (cancelled) return
      stop()
      setState('disconnected')
    })
    return stop
    // Só reconecta quando muda o computador ou o modo; atualizações do mesmo registro não piscam a tela.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [computer.id, computer.agent_id, interactive, attempt])
  return <div className="screen-viewer">
    <div className="screen-viewer-bar">
      <span role="status">{t(`view_${state}`)} · {t(interactive ? 'viewInteractive' : 'viewReadOnly')}</span>
      {state === 'disconnected' && <button type="button" className="text-button" onClick={() => { setState('connecting'); setAttempt((value) => value + 1) }}>{t('reconnect')}</button>}
    </div>
    {interactive && <p className="quiet-note" id={hintId}>{t('screenKeyboardHint')}</p>}
    <div className="screen-viewer-canvas" ref={target} role={interactive ? 'application' : 'img'} aria-label={t('screenLabel')} aria-describedby={interactive ? hintId : undefined} />
  </div>
}
