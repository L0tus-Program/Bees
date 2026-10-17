import { ApiError, asApiError } from '../../api/client'
import { getDeliveries } from '../../api/deliveryHistory'
import type { DeliveryPage, DeliveryScope } from '../../api/deliveryHistory'

export interface DeliveryView { page: DeliveryPage | null; loading: boolean; error: ApiError | null; cancelled: boolean }
/** Every reader belongs to one bee and one submitted query, including its cursor. */
export class DeliveryReader {
  state: DeliveryView = { page: null, loading: true, error: null, cancelled: false }
  private controller: AbortController | null = null
  private stopped = false
  constructor(private agentId: string, private scope: DeliveryScope, private publish: (value: DeliveryView) => void) {}
  private update(value: DeliveryView) { this.state = value; this.publish(value) }
  async load(more = false) {
    if (this.stopped || more && (!this.state.page?.next_cursor || this.state.error)) return
    this.controller?.abort()
    const controller = new AbortController(); this.controller = controller
    const previous = more ? this.state.page : null
    this.update({ page: previous, loading: true, error: null, cancelled: false })
    try {
      const incoming = await getDeliveries(this.agentId, this.scope, previous?.next_cursor ?? null, controller.signal)
      if (this.stopped || controller.signal.aborted) return
      if (previous && (incoming.until !== previous.until || incoming.items.some((item) => previous.items.some((old) => old.id === item.id && old.kind === item.kind)))) throw new ApiError('invalid_response')
      this.update({ page: { ...incoming, items: [...(previous?.items ?? []), ...incoming.items] }, loading: false, error: null, cancelled: false })
    } catch (failure) { if (!this.stopped && !controller.signal.aborted) this.update({ page: previous, loading: false, error: asApiError(failure), cancelled: false }) }
  }
  cancel() { this.controller?.abort(); this.update({ ...this.state, loading: false, cancelled: true }) }
  stop() { this.stopped = true; this.controller?.abort() }
}
