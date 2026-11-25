import { record } from '../../api/client'

export type PullIntent = Readonly<{ endpoint: string; model: string; clientRequestId: string }>
export type PullRecovery = { intent: PullIntent | null; blocked: boolean }
const modelPattern = /^[a-z0-9][a-z0-9._-]{0,99}(\/[a-z0-9][a-z0-9._-]{0,99})?(:[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const normalized = (endpoint: string) => new URL(endpoint || 'http://127.0.0.1:11434').href.replace(/\/+$/, '')
const key = (endpoint: string) => `bees.local-pull-pending.v1.${normalized(endpoint)}`

function checked(value: unknown, endpoint: string): PullIntent {
  if (!record(value) || Object.keys(value).sort().join(',') !== 'clientRequestId,endpoint,model'
    || typeof value.endpoint !== 'string' || normalized(value.endpoint) !== normalized(endpoint)
    || typeof value.model !== 'string' || !modelPattern.test(value.model) || value.model.toLowerCase().includes('cloud')
    || typeof value.clientRequestId !== 'string' || !uuid.test(value.clientRequestId)) throw new Error('invalid_intent')
  return { endpoint: value.endpoint, model: value.model, clientRequestId: value.clientRequestId }
}

export function readPullRecovery(endpoint: string): PullRecovery {
  try {
    const value = sessionStorage.getItem(key(endpoint))
    return { intent: value === null ? null : checked(JSON.parse(value), endpoint), blocked: false }
  } catch { return { intent: null, blocked: true } }
}

export function preservePullIntent(intent: PullIntent): boolean {
  try {
    const storageKey = key(intent.endpoint)
    const value = JSON.stringify(checked(intent, intent.endpoint))
    sessionStorage.setItem(storageKey, value)
    return sessionStorage.getItem(storageKey) === value
  } catch { return false }
}

export function clearPullIntent(endpoint: string): boolean {
  try {
    sessionStorage.removeItem(key(endpoint))
    return sessionStorage.getItem(key(endpoint)) === null
  } catch { return false }
}
