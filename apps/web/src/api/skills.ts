import { ApiError, record, request } from './client'
import { exact, uuid, timestamp } from './activity'

export interface SkillDefinition {
  name: string; purpose: string
  inputs: { name: string; description: string; required: boolean }[]
  steps: { title: string; instruction: string }[]
  resources: { environment: 'none' | 'own' | 'personal'; tools: string[] }
  output: string; completion: string
}
export interface SkillDocument { format: 'bees.skill/v1'; scope: 'agent'; definition: SkillDefinition }
export interface SkillRevision { skill_id: string; version: number; definition: SkillDefinition; content_hash: string; origin: 'human' | 'import'; created_at: string }
export interface Skill { id: string; agent_id: string; revision: number; current_version: number; enabled_version: number | null; created_at: string; updated_at: string; current: SkillRevision }
export interface SkillIntent { agentId: string; path: string; method?: 'PUT'; body: Record<string, unknown>; skillId?: string }
export interface ProcessReview { sources: string[]; review_note: string; pending: string; confirmed: boolean }
export interface ProcessReference { task_id: string; run_id: string; message_id: string; result_sha256: string }
export interface SkillProcessOrigin extends ProcessReference { skill_id: string; version: number; agent_id: string; review: ProcessReview; created_at: string; actor: 'user' }

const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0
const text = (value: unknown, max = 2048): value is string => typeof value === 'string' && value.trim().length > 0 && Array.from(value).length <= max && !Array.from(value).some((char) => char.charCodeAt(0) < 32 && !['\n', '\r', '\t'].includes(char))
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_.-]{0,63}$/.test(value)
export function isDefinition(value: unknown): value is SkillDefinition {
  if (!exact(value, ['name', 'purpose', 'inputs', 'steps', 'resources', 'output', 'completion']) || !text(value.name, 100) || !text(value.purpose) || !text(value.output) || !text(value.completion)
    || !Array.isArray(value.inputs) || value.inputs.length > 20 || !value.inputs.every((input) => exact(input, ['name', 'description', 'required']) && identifier(input.name) && text(input.description) && typeof input.required === 'boolean')
    || !Array.isArray(value.steps) || value.steps.length < 1 || value.steps.length > 30 || !value.steps.every((step) => exact(step, ['title', 'instruction']) && text(step.title, 100) && text(step.instruction))
    || !exact(value.resources, ['environment', 'tools']) || !['none', 'own', 'personal'].includes(String(value.resources.environment)) || !Array.isArray(value.resources.tools) || value.resources.tools.length > 20 || !value.resources.tools.every(identifier)) return false
  return new Set(value.inputs.map((input) => input.name)).size === value.inputs.length && new Set(value.resources.tools).size === value.resources.tools.length && new TextEncoder().encode(JSON.stringify(value)).byteLength <= 32000
}
export function isRevision(value: unknown): value is SkillRevision {
  return exact(value, ['skill_id', 'version', 'definition', 'content_hash', 'origin', 'created_at']) && uuid(value.skill_id) && positive(value.version) && isDefinition(value.definition)
    && typeof value.content_hash === 'string' && /^[0-9a-f]{64}$/.test(value.content_hash) && ['human', 'import'].includes(String(value.origin)) && timestamp(value.created_at)
}
export function checkedSkill(value: unknown, agentId: string, skillId?: string): Skill {
  if (!exact(value, ['id', 'agent_id', 'revision', 'current_version', 'enabled_version', 'created_at', 'updated_at', 'current']) || !uuid(value.id) || value.agent_id !== agentId || skillId && value.id !== skillId
    || !positive(value.revision) || !positive(value.current_version) || value.current_version > value.revision || !(value.enabled_version === null || value.enabled_version === value.current_version)
    || !timestamp(value.created_at) || !timestamp(value.updated_at) || !isRevision(value.current) || value.current.skill_id !== value.id || value.current.version !== value.current_version) throw new ApiError('invalid_response')
  return value as unknown as Skill
}
export function checkedDocument(value: unknown): SkillDocument {
  if (!exact(value, ['format', 'scope', 'definition']) || value.format !== 'bees.skill/v1' || value.scope !== 'agent' || !isDefinition(value.definition)) throw new ApiError('invalid_request')
  return value as unknown as SkillDocument
}
function base(agentId: string) {
  if (!uuid(agentId)) throw new ApiError('invalid_request')
  return `/agents/${agentId}/skills`
}
export async function listSkills(agentId: string, offset = 0, signal?: AbortSignal): Promise<Skill[]> {
  if (!Number.isSafeInteger(offset) || offset < 0) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}?limit=50&offset=${offset}`, { authenticated: true, signal })
  if (!Array.isArray(value) || value.length > 50) throw new ApiError('invalid_response')
  const items = value.map((item) => checkedSkill(item, agentId))
  if (new Set(items.map((item) => item.id)).size !== items.length) throw new ApiError('invalid_response')
  await Promise.all(items.map((item) => verifyRevisionHash(item.current)))
  return items
}
export async function skillRevisions(agentId: string, skillId: string, offset = 0, signal?: AbortSignal): Promise<SkillRevision[]> {
  if (!uuid(skillId) || !Number.isSafeInteger(offset) || offset < 0) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/${skillId}/revisions?limit=50&offset=${offset}`, { authenticated: true, signal })
  if (!Array.isArray(value) || value.length > 50 || !value.every((item) => isRevision(item) && item.skill_id === skillId) || value.some((item, index) => index > 0 && item.version >= value[index - 1].version)) throw new ApiError('invalid_response')
  await Promise.all((value as SkillRevision[]).map(verifyRevisionHash))
  return value as SkillRevision[]
}
export async function exportSkill(agentId: string, skillId: string, version: number): Promise<SkillDocument> {
  if (!uuid(skillId) || !positive(version)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/${skillId}/revisions/${version}/export`, { authenticated: true })
  try { return checkedDocument(value) } catch { throw new ApiError('invalid_response') }
}
export function skillIntent(agentId: string, definition: SkillDefinition, previous?: Skill): SkillIntent {
  if (!isDefinition(definition) || previous && previous.agent_id !== agentId) throw new ApiError('invalid_request')
  return { agentId, path: previous ? `${base(agentId)}/${previous.id}/revisions` : base(agentId), skillId: previous?.id,
    body: { client_request_id: crypto.randomUUID(), definition, ...(previous ? { expected_revision: previous.revision } : {}) } }
}
export function enableIntent(skill: Skill): SkillIntent {
  return { agentId: skill.agent_id, skillId: skill.id, path: `${base(skill.agent_id)}/${skill.id}/enabled`, method: 'PUT',
    body: { client_request_id: crypto.randomUUID(), expected_revision: skill.revision, version: skill.current_version, enabled: skill.enabled_version === null } }
}
export function importIntent(agentId: string, document: unknown): SkillIntent {
  return { agentId, path: `${base(agentId)}/import`, body: { client_request_id: crypto.randomUUID(), document: checkedDocument(document) } }
}
export async function sendSkill(intent: SkillIntent, signal?: AbortSignal): Promise<Skill> {
  if (!record(intent.body) || !uuid(intent.body.client_request_id)) throw new ApiError('invalid_request')
  const { agentId, skillId, path, method } = intent
  // Capture identity and the complete declarative command before any await.
  const body = { ...intent.body }
  const enabling = method === 'PUT' || path.endsWith('/enabled')
  if (enabling && (method !== 'PUT' || !uuid(skillId) || path !== `${base(agentId)}/${skillId}/enabled`
    || !exact(body, ['client_request_id', 'expected_revision', 'version', 'enabled'])
    || !positive(body.expected_revision) || body.expected_revision === Number.MAX_SAFE_INTEGER
    || !positive(body.version) || body.version > body.expected_revision || typeof body.enabled !== 'boolean')) throw new ApiError('invalid_request')
  let definition: SkillDefinition | undefined
  if (!enabling) {
    if (method !== undefined) throw new ApiError('invalid_request')
    const root = base(agentId)
    if (skillId !== undefined) {
      if (!uuid(skillId) || path !== `${root}/${skillId}/revisions`
        || !exact(body, ['client_request_id', 'expected_revision', 'definition'])
        || !positive(body.expected_revision) || body.expected_revision === Number.MAX_SAFE_INTEGER) throw new ApiError('invalid_request')
      definition = body.definition as SkillDefinition
    } else if (path === `${root}/import`) {
      if (!exact(body, ['client_request_id', 'document'])) throw new ApiError('invalid_request')
      definition = checkedDocument(body.document).definition
    } else if (path === `${root}/from-process`) {
      checkedProcessIntent(intent, agentId)
      definition = body.definition as SkillDefinition
    } else if (path === root && exact(body, ['client_request_id', 'definition'])) {
      definition = body.definition as SkillDefinition
    } else throw new ApiError('invalid_request')
    if (!isDefinition(definition)) throw new ApiError('invalid_request')
  }
  const snapshot = JSON.parse(JSON.stringify(body)) as Record<string, unknown>
  const expectedDefinition = definition ? canonical(definition) : undefined
  const result = checkedSkill(await request(path, { body: snapshot, method, authenticated: true, signal }), agentId, skillId)
  if (enabling && (result.revision !== (body.expected_revision as number) + 1 || result.current_version !== body.version
    || result.enabled_version !== (body.enabled ? body.version : null))) throw new ApiError('invalid_response')
  if (!enabling && (canonical(result.current.definition) !== expectedDefinition || result.enabled_version !== null
    || result.current.origin !== (path.endsWith('/import') ? 'import' : 'human')
    || result.revision !== (skillId ? (snapshot.expected_revision as number) + 1 : 1)
    || (skillId ? result.current_version < 2 : result.current_version !== 1))) throw new ApiError('invalid_response')
  await verifyRevisionHash(result.current)
  if (path === `${base(agentId)}/from-process`) {
    try {
      const origin = await getProcessOrigin(agentId, result.id, result.current_version, signal)
      if (!origin || origin.task_id !== snapshot.task_id || origin.run_id !== snapshot.run_id
        || origin.message_id !== snapshot.message_id || origin.result_sha256 !== snapshot.result_sha256
        || canonical(origin.review) !== canonical(snapshot.review)) throw new ApiError('invalid_response')
    } catch (failure) {
      if (signal?.aborted) throw failure
      // A failed read cannot reject a POST that has already returned a valid receipt.
      // Keep it uncertain even on HTTP 4xx so recovery retains the original UUID/body.
      throw new ApiError('invalid_response')
    }
  }
  return result
}

// Object key order is irrelevant; array order and user text remain significant.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (record(value)) return '{' + Object.keys(value).sort().map((key) => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}'
  return JSON.stringify(value)
}

async function verifyRevisionHash(revision: SkillRevision): Promise<void> {
  try {
    // SkillService._canonical uses Python json.dumps with ensure_ascii=True.
    // Escape UTF-16 units (including both halves of emoji), DEL and non-ASCII.
    // Definition keys are fixed ASCII identifiers; list order and text stay exact.
    const encoded = canonical(revision.definition).replace(/[\u007f-\uffff]/g, (char) => '\\u' + char.charCodeAt(0).toString(16).padStart(4, '0'))
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(encoded))
    const hash = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('')
    if (hash !== revision.content_hash) throw new ApiError('invalid_response')
  } catch {
    // Even crypto unavailability cannot establish rejection of a completed POST.
    throw new ApiError('invalid_response')
  }
}

function isProcessReview(value: unknown): value is ProcessReview {
  if (!exact(value, ['sources', 'review_note', 'pending', 'confirmed']) || value.confirmed !== true || !text(value.review_note) || !text(value.pending)
    || !Array.isArray(value.sources) || value.sources.length < 1 || value.sources.length > 10 || new Set(value.sources).size !== value.sources.length) return false
  return value.sources.every((source) => {
    if (!text(source) || /[\s\\]/.test(source)) return false
    try { const url = new URL(source); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password } catch { return false }
  })
}
function isProcessReference(value: Record<string, unknown>): boolean {
  return uuid(value.task_id) && uuid(value.run_id) && uuid(value.message_id) && typeof value.result_sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.result_sha256)
}
export function checkedProcessIntent(value: unknown, agentId: string): SkillIntent {
  if (!exact(value, ['agentId', 'path', 'body']) || value.agentId !== agentId || value.path !== `${base(agentId)}/from-process`
    || !exact(value.body, ['client_request_id', 'definition', 'task_id', 'run_id', 'message_id', 'result_sha256', 'review'])
    || !uuid(value.body.client_request_id) || !isDefinition(value.body.definition) || !isProcessReference(value.body) || !isProcessReview(value.body.review)) throw new ApiError('invalid_request')
  return value as unknown as SkillIntent
}
export function processSkillIntent(agentId: string, definition: SkillDefinition, source: ProcessReference, review: ProcessReview): SkillIntent {
  return checkedProcessIntent({ agentId, path: `${base(agentId)}/from-process`, body: { client_request_id: crypto.randomUUID(), definition, ...source, review } }, agentId)
}
export async function getProcessOrigin(agentId: string, skillId: string, version: number, signal?: AbortSignal): Promise<SkillProcessOrigin | null> {
  if (!uuid(skillId) || !positive(version)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/${skillId}/revisions/${version}/process`, { authenticated: true, signal })
  if (value === null) return null
  if (!exact(value, ['skill_id', 'version', 'agent_id', 'task_id', 'run_id', 'message_id', 'result_sha256', 'review', 'created_at', 'actor'])
    || value.agent_id !== agentId || value.skill_id !== skillId || value.version !== version || !isProcessReference(value) || !isProcessReview(value.review)
    || !timestamp(value.created_at) || value.actor !== 'user') throw new ApiError('invalid_response')
  return value as unknown as SkillProcessOrigin
}
