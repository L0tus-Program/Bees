import { record } from '../../api/client'

export type RemovalIntent = Readonly<{ endpoint: string; model: string }>
export type RemovalRecovery = { intent: RemovalIntent | null; blocked: boolean }
const modelPattern = /^[a-z0-9][a-z0-9._-]{0,99}(\/[a-z0-9][a-z0-9._-]{0,99})?(:[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$/
const normalized = (endpoint: string) => new URL(endpoint || 'http://127.0.0.1:11434').href.replace(/\/+$/, '')
const key = (endpoint: string) => `bees.local-removal-pending.v1.${normalized(endpoint)}`

function checked(value: unknown, endpoint: string): RemovalIntent {
  if (!record(value) || Object.keys(value).sort().join(',') !== 'endpoint,model'
    || typeof value.endpoint !== 'string' || normalized(value.endpoint) !== normalized(endpoint)
    || typeof value.model !== 'string' || !modelPattern.test(value.model) || value.model.toLowerCase().includes('cloud')) throw new Error('invalid_intent')
  return { endpoint: value.endpoint, model: value.model }
}

export function readRemovalRecovery(endpoint: string): RemovalRecovery {
  try {
    const value = sessionStorage.getItem(key(endpoint))
    return { intent: value === null ? null : checked(JSON.parse(value), endpoint), blocked: false }
  } catch { return { intent: null, blocked: true } }
}

export function preserveRemovalIntent(intent: RemovalIntent): boolean {
  try {
    const storageKey = key(intent.endpoint)
    const value = JSON.stringify(checked(intent, intent.endpoint))
    sessionStorage.setItem(storageKey, value)
    return sessionStorage.getItem(storageKey) === value
  } catch { return false }
}

export function clearRemovalIntent(endpoint: string): boolean {
  try {
    sessionStorage.removeItem(key(endpoint))
    return sessionStorage.getItem(key(endpoint)) === null
  } catch { return false }
}
