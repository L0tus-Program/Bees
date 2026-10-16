import { asApiError, ApiError } from '../../api/client'
import { getArtifacts, getConversationArtifacts } from './api'
import type { ArtifactPage, ArtifactRecord } from './api'

export interface ArtifactView { page: ArtifactPage | null; loading: boolean; error: ApiError | null }
const immutable = ['id', 'agent_id', 'conversation_id', 'task_id', 'source', 'run_id', 'name', 'media_type', 'size_bytes', 'sha256', 'version', 'series_id', 'previous_id', 'created_at'] as const
function checkSnapshots(items: ArtifactRecord[], known: Map<string, ArtifactRecord>) {
  for (const item of items) {
    const previous = known.get(item.id)
    if (previous && (item.revision < previous.revision || Date.parse(item.updated_at) < Date.parse(previous.updated_at)
      || immutable.some((key) => item[key] !== previous[key]) || item.revision === previous.revision && (item.status !== previous.status || item.updated_at !== previous.updated_at)
      || previous.status !== 'draft' && item.status !== previous.status && !(previous.status === 'ready' && item.status === 'deleted'))) throw new ApiError('invalid_response')
  }
}

// One reader belongs to one task. Stop aborts the transport and fences late replies.
export class ArtifactReader {
  state: ArtifactView = { page: null, loading: true, error: null }
  private controller: AbortController | null = null
  private known = new Map<string, ArtifactRecord>()
  private stopped = false
  constructor(private agentId: string, private taskId: string, private publish: (state: ArtifactView) => void, private conversation = false) {}
  private update(state: ArtifactView) { this.state = state; this.publish(state) }
  async load(offset = 0) {
    if (this.stopped) return
    this.controller?.abort()
    const operation = new AbortController(); this.controller = operation
    this.update({ ...this.state, loading: true, error: null })
    try {
      const incoming = await (this.conversation ? getConversationArtifacts(this.agentId, this.taskId, offset, operation.signal) : getArtifacts(this.agentId, this.taskId, offset, operation.signal))
      if (operation.signal.aborted || this.stopped) return
      checkSnapshots(incoming.items, this.known)
      incoming.items.forEach((item) => this.known.set(item.id, item))
      const items = offset > 0 && this.state.page ? [...this.state.page.items.filter((item) => !incoming.items.some((next) => next.id === item.id)), ...incoming.items] : incoming.items
      this.update({ page: { ...incoming, items }, loading: false, error: null })
    } catch (failure) { if (!operation.signal.aborted && !this.stopped) this.update({ ...this.state, loading: false, error: asApiError(failure) }) }
  }
  stop() { this.stopped = true; this.controller?.abort() }
}
