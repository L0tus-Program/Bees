import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { loadEnvironments } from './state'

export function EnvironmentSummary({ agentId, onOpen }: { agentId: string; onOpen: () => void }) {
  const { t } = useTranslation('environments')
  const [attempt, setAttempt] = useState(0); const [state, setState] = useState('loading')
  useEffect(() => {
    const controller = new AbortController()
    void loadEnvironments(agentId, controller.signal).then((workspace) => {
      if (!controller.signal.aborted) {
        const active = workspace.environments.find((environment) => environment.status !== 'cancelled')
        setState(active ? `status_${active.status}` : 'empty')
      }
    }, () => { if (!controller.signal.aborted) setState('stale') })
    return () => controller.abort()
  }, [agentId, attempt])
  useEffect(() => {
    const changed = () => { setState('loading'); setAttempt((value) => value + 1) }
    window.addEventListener('bees:environments-changed', changed)
    return () => window.removeEventListener('bees:environments-changed', changed)
  }, [])
  return <div className="environment-card"><h3>{t('summaryTitle')}</h3><p role={state === 'loading' ? 'status' : undefined}>{t(state)}</p><button className="button secondary" type="button" onClick={onOpen}>{t('open')}</button></div>
}
