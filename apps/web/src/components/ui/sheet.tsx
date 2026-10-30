import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/** Painel lateral (gaveta) acessível: foco preso, Esc fecha, título obrigatório. */
export function Sheet({ open, onOpenChange, title, description, closeLabel, side = 'right', className, children }: {
  open: boolean; onOpenChange: (open: boolean) => void; title: ReactNode; description?: ReactNode; closeLabel: string
  side?: 'right' | 'left'; className?: string; children: ReactNode
}) {
  return <Dialog.Root open={open} onOpenChange={onOpenChange}>
    <Dialog.Portal>
      <Dialog.Overlay className="ui fixed inset-0 z-40 bg-black/40" />
      <Dialog.Content className={cn('ui fixed top-0 z-50 flex h-dvh w-full max-w-md flex-col bg-background text-foreground shadow-xl outline-none', side === 'right' ? 'right-0 border-l' : 'left-0 border-r', className)} aria-describedby={description ? undefined : undefined}>
        <div className="flex items-center justify-between gap-2 border-b px-4 py-3">
          <Dialog.Title className="text-base font-semibold">{title}</Dialog.Title>
          <Dialog.Close className="inline-flex size-8 cursor-pointer items-center justify-center rounded-md border-0 bg-transparent text-foreground hover:bg-muted" aria-label={closeLabel}><X className="size-4" /></Dialog.Close>
        </div>
        {description ? <Dialog.Description className="px-4 pt-3 text-sm text-muted-foreground">{description}</Dialog.Description> : <Dialog.Description className="sr-only">{title}</Dialog.Description>}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>
}
