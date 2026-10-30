import * as TooltipPrimitive from '@radix-ui/react-tooltip'
import type { ComponentProps, ReactNode } from 'react'
import { cn } from '@/lib/utils'

export const TooltipProvider = TooltipPrimitive.Provider

export function Tooltip({ label, children, side = 'top' }: { label: ReactNode; children: ReactNode; side?: ComponentProps<typeof TooltipPrimitive.Content>['side'] }) {
  return <TooltipPrimitive.Root delayDuration={300}>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content side={side} sideOffset={6} className={cn('ui z-50 rounded-md bg-foreground px-2 py-1 text-xs text-background shadow-md')}>{label}</TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>
}
