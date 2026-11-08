import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { getSafety } from '../../api/safety'
import { SafetyControl } from '../safety/SafetyControl'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'

/** Estado da parada global sempre visível; o controle completo abre num painel. */
export function SafetyButton({ closePanelRef }: { closePanelRef?: RefObject<(() => void) | null> }) {
  const { t } = useTranslation('workspace')
  const [stopped, setStopped] = useState<boolean | null>(null)
  const [open, setOpen] = useState(false)
  const [portalContainer, setPortalContainer] = useState<HTMLElement>()
  const opener = useRef<HTMLButtonElement>(null)
  const restoreFocus = useRef(false)
  const closePanel = useCallback(() => {
    restoreFocus.current = true
    setOpen(false)
  }, [])
  useLayoutEffect(() => {
    if (!closePanelRef || !open) return
    closePanelRef.current = closePanel
    return () => { closePanelRef.current = null }
  }, [closePanel, closePanelRef, open])
  useEffect(() => {
    if (open || !restoreFocus.current) return
    restoreFocus.current = false
    // A gaveta já saiu do DOM, inclusive quando foi fechada antes do foco inicial.
    opener.current?.focus({ preventScroll: true })
  }, [open])
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
    <Sheet open={open} onOpenChange={(next) => { if (!next) closePanel() }} title={t('safety')} closeLabel={t('closePanel')} side="left" portalContainer={portalContainer}
      onCloseAutoFocus={(event) => event.preventDefault()}>
      <div className="legacy-scope"><SafetyControl /></div>
    </Sheet>
  </>
}
