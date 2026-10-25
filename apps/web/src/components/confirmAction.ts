// Pedido de confirmação atendido pelo ConfirmHost montado no App; sem host, usa o nativo.
export type ConfirmRequest = { message: string; confirmLabel?: string; resolve: (value: boolean) => void }
type Listener = (request: ConfirmRequest) => void
let listener: Listener | null = null

export function registerConfirmHost(next: Listener | null) { listener = next }

export function confirmAction(message: string, confirmLabel?: string): Promise<boolean> {
  if (!listener) return Promise.resolve(window.confirm(message))
  const current = listener
  return new Promise((resolve) => current({ message, confirmLabel, resolve }))
}
