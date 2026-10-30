import type { ComponentProps } from 'react'
import { cn } from '@/lib/utils'

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return <textarea data-slot="textarea" className={cn('field-sizing-content min-h-12 w-full resize-none rounded-lg bg-transparent px-3 py-2 text-[15px] leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-50', className)} {...props} />
}
