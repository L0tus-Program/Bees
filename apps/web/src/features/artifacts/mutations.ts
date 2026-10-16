import { ApiError, asApiError } from '../../api/client'
import { checkedArtifact, getConversationArtifact, publishArtifact, uuid } from './api'
import type { ArtifactRecord } from './api'

export const MAX_FILE_BYTES = 25 * 1024 * 1024
export const MAX_CONTEXT_BYTES = 65536
export const formats = { txt: 'text/plain', md: 'text/markdown', csv: 'text/csv', json: 'application/json' } as const
export interface Candidate { name: string; media_type: string; size_bytes: number; sha256: string; bytes: Uint8Array }
export interface ArtifactIntent { id: string; agentId: string; conversationId: string; name: string; media_type: string; size_bytes: number; sha256: string; previous: ArtifactRecord | null }
export interface PublicationState { intent: ArtifactIntent | null; candidate: Candidate | null; phase: 'idle' | 'sending' | 'uncertain' | 'checking' | 'retryable' | 'conflict' | 'rejected' | 'confirmed'; error: ApiError | null; artifact: ArtifactRecord | null }
export const idlePublication: PublicationState = { intent: null, candidate: null, phase: 'idle', error: null, artifact: null }

export function validName(name: string) { return !!name.trim() && name.trim() === name && Array.from(name).length <= 200 && !/[/\\\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(name) }
export async function prepareBytes(name: string, mediaType: string, bytes: Uint8Array, signal?: AbortSignal): Promise<Candidate> {
  if (!validName(name) || !Object.values(formats).includes(mediaType as typeof formats[keyof typeof formats])) throw new ApiError('artifact_invalid_format')
  if (bytes.byteLength > MAX_FILE_BYTES) throw new ApiError('artifact_upload_too_large')
  try { const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes); if (text.includes('\0')) throw new Error(); if (mediaType === 'application/json') JSON.parse(text) } catch { throw new ApiError('artifact_invalid_content') }
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes.slice().buffer))
  if (signal?.aborted) throw new ApiError('request_cancelled')
  return { name, media_type: mediaType, size_bytes: bytes.byteLength, sha256: Array.from(hash, (value) => value.toString(16).padStart(2, '0')).join(''), bytes }
}
export async function prepareFile(file: File, signal?: AbortSignal) {
  const extension = file.name.split('.').at(-1)?.toLowerCase()
  if (!extension || !(extension in formats)) throw new ApiError('artifact_invalid_format')
  if (file.size > MAX_FILE_BYTES) throw new ApiError('artifact_upload_too_large')
  return prepareBytes(file.name, formats[extension as keyof typeof formats], new Uint8Array(await file.arrayBuffer()), signal)
}
export function matchesCandidate(intent: ArtifactIntent, candidate: Candidate) { return (['name', 'media_type', 'size_bytes', 'sha256'] as const).every((key) => intent[key] === candidate[key]) }
export function checkedReceipt(intent: ArtifactIntent, artifact: ArtifactRecord, allowIncomplete = false) {
  if (artifact.id !== intent.id || artifact.agent_id !== intent.agentId || artifact.conversation_id !== intent.conversationId || (allowIncomplete ? !['draft', 'ready', 'failed', 'deleted'].includes(artifact.status) : artifact.status !== 'ready')
    || artifact.name !== intent.name || artifact.media_type !== intent.media_type || artifact.size_bytes !== intent.size_bytes || artifact.sha256 !== intent.sha256
    || artifact.previous_id !== (intent.previous?.id ?? null) || artifact.version !== (intent.previous ? intent.previous.version + 1 : 1)
    || artifact.source !== (intent.previous ? 'user_revision' : 'user_upload') || artifact.task_id !== (intent.previous?.task_id ?? null) || artifact.run_id !== null
    || artifact.series_id !== (intent.previous ? intent.previous.series_id ?? intent.previous.id : intent.id)) throw new ApiError('invalid_response')
  return artifact
}

// One command retains its UUID and bytes until an authenticated receipt resolves it.
export class Publication {
  state: PublicationState = idlePublication
  private controller: AbortController | null = null
  private stopped = false
  constructor(private agentId: string, private conversationId: string, private notify: (state: PublicationState) => void) {}
  private update(next: PublicationState) { this.state = next; this.notify(next) }
  restore(intent: ArtifactIntent) { if (intent.agentId !== this.agentId || intent.conversationId !== this.conversationId) return; this.update({ ...idlePublication, intent, phase: 'uncertain' }) }
  choose(candidate: Candidate, previous: ArtifactRecord | null) {
    if (this.state.intent && !['confirmed', 'conflict', 'rejected'].includes(this.state.phase)) throw new ApiError('artifact_pending_publication')
    const intent = { id: crypto.randomUUID(), agentId: this.agentId, conversationId: this.conversationId, name: candidate.name, media_type: candidate.media_type, size_bytes: candidate.size_bytes, sha256: candidate.sha256, previous }
    this.update({ intent, candidate, phase: 'idle', artifact: null, error: null })
  }
  reselect(candidate: Candidate) { if (!this.state.intent || !matchesCandidate(this.state.intent, candidate)) throw new ApiError('artifact_candidate_mismatch'); this.update({ ...this.state, candidate }) }
  clear() { if (this.state.intent && !['idle', 'confirmed', 'conflict', 'rejected'].includes(this.state.phase)) return; this.update(idlePublication) }
  async send() {
    const { intent, candidate, phase } = this.state
    if (this.stopped || !intent || !candidate || !['idle', 'retryable'].includes(phase) || !matchesCandidate(intent, candidate)) return
    const operation = new AbortController(); this.controller = operation
    this.update({ ...this.state, phase: 'sending', error: null })
    try {
      const artifact = checkedReceipt(intent, await publishArtifact(intent, candidate.bytes, operation.signal))
      if (!operation.signal.aborted && !this.stopped) this.update({ ...this.state, phase: 'confirmed', artifact, candidate: null, error: null })
    } catch (error) {
      if (!this.stopped && !operation.signal.aborted) {
        const failure = asApiError(error)
        const refused = [413, 422].includes(failure.status) || ['artifact_upload_busy', 'upload_disconnected', 'body_timeout', 'invalid_length', 'artifact_content_invalid', 'artifact_metadata_invalid', 'artifact_content_mismatch', 'artifact_name_invalid', 'artifact_media_type_unsupported', 'artifact_too_large', 'artifact_conversation_archived'].includes(failure.code)
        const conflict = failure.status === 409 && ['artifact_revision_conflict', 'revision_conflict', 'artifact_version_conflict', 'artifact_version_pending'].includes(failure.code)
        this.update({ ...this.state, phase: refused ? 'rejected' : conflict ? 'conflict' : 'uncertain', error: failure })
      }
    }
  }
  async reconcile() {
    const { intent } = this.state
    if (this.stopped || !intent || ['sending', 'checking', 'confirmed'].includes(this.state.phase)) return
    const operation = new AbortController(); this.controller = operation
    this.update({ ...this.state, phase: 'checking', error: null })
    try {
      const artifact = checkedReceipt(intent, await getConversationArtifact(this.agentId, this.conversationId, intent.id, operation.signal), true)
      if (!this.stopped && !operation.signal.aborted) {
        const prior = this.state.artifact
        if (prior && (artifact.revision < prior.revision || Date.parse(artifact.updated_at) < Date.parse(prior.updated_at) || artifact.revision === prior.revision && (artifact.status !== prior.status || artifact.updated_at !== prior.updated_at))) throw new ApiError('invalid_response')
        this.update({ ...this.state, phase: artifact.status === 'ready' ? 'confirmed' : artifact.status === 'draft' ? 'retryable' : 'rejected', artifact, ...(artifact.status === 'ready' ? { candidate: null } : {}), error: null })
      }
    } catch (error) {
      if (!this.stopped && !operation.signal.aborted) { const failure = asApiError(error); this.update({ ...this.state, phase: failure.status === 404 ? 'retryable' : 'uncertain', error: failure }) }
    }
  }
  cancelTransport() { this.controller?.abort(); if (['sending', 'checking'].includes(this.state.phase)) this.update({ ...this.state, phase: 'uncertain', error: null }) }
  stop() { this.stopped = true; this.controller?.abort() }
}

const storageKey = (agentId: string, conversationId: string) => `bees:artifact-publication:${agentId}:${conversationId}`
export function rememberPublication(state: PublicationState) {
  const intent = state.intent
  if (!intent || typeof sessionStorage === 'undefined') return
  try {
    if (['confirmed', 'conflict', 'rejected'].includes(state.phase)) sessionStorage.removeItem(storageKey(intent.agentId, intent.conversationId))
    else if (state.phase !== 'idle') sessionStorage.setItem(storageKey(intent.agentId, intent.conversationId), JSON.stringify(intent))
  } catch { /* Bytes and text remain only in memory, even if storage is unavailable. */ }
}
export function restorePublication(agentId: string, conversationId: string): ArtifactIntent | null {
  if (typeof sessionStorage === 'undefined') return null
  try {
    const raw = sessionStorage.getItem(storageKey(agentId, conversationId)); if (!raw || raw.length > 8192) return null
    const value = JSON.parse(raw) as ArtifactIntent
    if (!value || Object.keys(value).length !== 8 || value.agentId !== agentId || value.conversationId !== conversationId || !uuid(value.id)
      || typeof value.name !== 'string' || !validName(value.name) || !Object.values(formats).includes(value.media_type as typeof formats[keyof typeof formats])
      || !Number.isSafeInteger(value.size_bytes) || value.size_bytes < 0 || value.size_bytes > MAX_FILE_BYTES || !/^[a-f0-9]{64}$/.test(value.sha256)) return null
    if (value.previous !== null) checkedArtifact(value.previous, agentId, { conversationId })
    return value
  } catch { return null }
}

export function isAttachmentSelection(items: ArtifactRecord[], agentId: string, conversationId: string) {
  return uuid(agentId) && uuid(conversationId) && items.length <= 4 && new Set(items.map((item) => item.id)).size === items.length
    && items.every((item) => item.agent_id === agentId && item.conversation_id === conversationId && item.status === 'ready' && item.sha256 !== null && Object.values(formats).includes(item.media_type as typeof formats[keyof typeof formats]))
    && items.reduce((sum, item) => sum + item.size_bytes, 0) <= MAX_CONTEXT_BYTES
}
