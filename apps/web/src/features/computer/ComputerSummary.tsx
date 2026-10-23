import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getComputers, getExecutors } from '../../api/computers'
import type { ComputerState, Executor } from '../../api/computers'

type Summary = { state: 'loading' | 'error' } | { state: 'ready'; computers: ComputerState; personal: Executor[] }

export function ComputerSummary({ agentId, onOpen }: { agentId: string; onOpen: () => void }) {
  const { t } = useTranslation('computer')
  const [summary, setSummary] = useState<Summary>({ state: 'loading' })
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([getComputers(agentId, controller.signal), getExecutors('personal', controller.signal)]).then(([computers, personal]) => {
      if (!controller.signal.aborted) setSummary({ state: 'ready', computers, personal: personal.filter((item) => item.status === 'active') })
    }, () => { if (!controller.signal.aborted) setSummary({ state: 'error' }) })
    return () => controller.abort()
  }, [agentId])
  const ready = summary.state === 'ready' ? summary : null
  const computer = ready?.computers.computers.find((item) => item.status !== 'removed')
  const own = !ready ? t(summary.state === 'loading' ? 'summaryLoading' : 'summaryError')
    : computer ? `${t(`status_${computer.status}`)} · ${computer.online ? t('online') : t('offline_label')}${computer.control_holder === 'human' ? ` · ${t('controlTaken')}` : ''}`
      : ready.computers.runtime_online ? t('noComputer') : t('runtimeUnpaired')
  const personal = !ready ? null : ready.personal.length
    ? t('summaryConnectors', { online: ready.personal.filter((item) => item.online).length, count: ready.personal.length }) : t('summaryNoConnector')
  return <>
    <div className="environment-card"><h3>{t('title')}</h3><p role={summary.state === 'loading' ? 'status' : undefined}>{own}</p><button className="button secondary" type="button" onClick={onOpen}>{t('summaryOpen')}</button></div>
    <div className="environment-card"><h3>{t('personalTitle')}</h3><p>{personal ?? t('summaryLoading')}</p></div>
  </>
}
