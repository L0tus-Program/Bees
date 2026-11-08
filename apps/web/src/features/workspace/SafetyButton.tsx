import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getSafety } from '../../api/safety'
import { SafetyControl } from '../safety/SafetyControl'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

/** Estado da parada global sempre visível; o controle completo abre num painel. */
export function SafetyButton() {
  const { t } = useTranslation('workspace')
  const [stopped, setStopped] = useState<boolean | null>(null)
  const [open, setOpen] = useState(false)
  const [portalContainer, setPortalContainer] = useState<HTMLElement>()
  const opener = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    let active = true
    const read = () => { getSafety().then((state) => { if (active) setStopped(state.status === 'stopped') }, () => undefined) }
    read()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') read() }, 10000)
    return () => { active = false; clearInterval(timer) }
  }, [open])
  return <>
    <Button variant="ghost" size="sm" className="w-full justify-start" ref={opener}
      aria-haspopup="dialog" aria-expanded={open} onClick={(event) => {
        // No celular, mantenha a gaveta interna dentro do escopo de foco do menu.
        setPortalContainer(event.currentTarget.closest('[role="dialog"]') ? event.currentTarget.parentElement ?? undefined : undefined)
        setOpen(true)
      }}>
      <span className={cn('size-2 rounded-full', stopped ? 'bg-destructive' : 'bg-emerald-500')} aria-hidden="true" />
      {t(stopped ? 'safetyStopped' : 'safetyRunning')}
    </Button>
    <Sheet open={open} onOpenChange={setOpen} title={t('safety')} closeLabel={t('closePanel')} side="left" portalContainer={portalContainer}
      onCloseAutoFocus={(event) => {
        const trigger = opener.current
        if (!trigger?.isConnected) return
        event.preventDefault()
        // O menu móvel também é modal. Deixe a gaveta interna concluir a troca do escopo de foco.
        requestAnimationFrame(() => {
          if (trigger.isConnected) trigger.focus()
        })
      }}>
      <div className="legacy-scope"><SafetyControl /></div>
    </Sheet>
  </>
}
