import { ApiError, record, request } from './client'

export type BudgetStatus = 'active' | 'disabled'
export interface BudgetLimit {
  token_limit: number; window_seconds: number; output_allowance: number
  status: BudgetStatus; revision: number; updated_at: string
}
export interface BudgetSummary {
  limit: BudgetLimit | null; window_seconds: number
  counted_tokens: number; reported_tokens: number; remaining_tokens: number | null
  open_reservations: number; unknown_entries: number; entries: number
}
export interface BudgetInput {
  token_limit: number; window_seconds: number; output_allowance: number; status: BudgetStatus
  expected_revision?: number
}

const LIMIT_KEYS = ['output_allowance', 'revision', 'status', 'token_limit', 'updated_at', 'window_seconds']
const SUMMARY_KEYS = ['counted_tokens', 'entries', 'limit', 'open_reservations', 'remaining_tokens', 'reported_tokens', 'unknown_entries', 'window_seconds']
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join() === keys.join()

function isLimit(value: unknown): value is BudgetLimit {
  return record(value) && exact(value, LIMIT_KEYS)
    && count(value.token_limit) && (value.token_limit as number) > 0
    && count(value.output_allowance) && (value.output_allowance as number) > 0
    && count(value.window_seconds) && (value.window_seconds as number) >= 3600
    && (value.status === 'active' || value.status === 'disabled')
    && Number.isSafeInteger(value.revision) && (value.revision as number) > 0
    && typeof value.updated_at === 'string' && !Number.isNaN(Date.parse(value.updated_at))
}

// Payload extra, contagem negativa ou restante inventado são recusados como resposta inválida.
export function isBudgetSummary(value: unknown): value is BudgetSummary {
  if (!record(value) || !exact(value, SUMMARY_KEYS)) return false
  if (!['counted_tokens', 'reported_tokens', 'open_reservations', 'unknown_entries', 'entries', 'window_seconds'].every((key) => count(value[key]))) return false
  if (value.limit !== null && !isLimit(value.limit)) return false
  const active = value.limit !== null && (value.limit as BudgetLimit).status === 'active'
  if (active ? !count(value.remaining_tokens) : value.remaining_tokens !== null) return false
  return value.limit === null || (value.limit as BudgetLimit).window_seconds === value.window_seconds
}

const path = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/budget`

export async function getBudget(agentId: string, signal?: AbortSignal): Promise<BudgetSummary> {
  const value = await request(path(agentId), { authenticated: true, signal })
  if (!isBudgetSummary(value)) throw new ApiError('invalid_response')
  return value
}

export async function putBudget(agentId: string, body: BudgetInput, signal?: AbortSignal): Promise<BudgetSummary> {
  const value = await request(path(agentId), { body, method: 'PUT', authenticated: true, signal })
  // A gravação confirmada precisa avançar exatamente a revisão lida: nunca regredir.
  const expected = (body.expected_revision ?? 0) + 1
  if (!isBudgetSummary(value) || value.limit === null || value.limit.revision !== expected) throw new ApiError('invalid_response')
  return value
}
