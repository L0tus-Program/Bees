import { ApiError, record, request } from './client'
import { checkedObservation, instant, isInterval, isObservation, timestamp } from './activity'
import type { Interval, Observation } from './activity'

export type BudgetStatus = 'active' | 'disabled'
export interface BudgetLimit {
  token_limit: number; window_seconds: number; output_allowance: number
  status: BudgetStatus; revision: number; updated_at: string
}
export interface BudgetSummary {
  agent_id: string
  snapshot: Observation; interval: Interval
  limit: BudgetLimit | null; window_seconds: number
  counted_tokens: number; reported_tokens: number; remaining_tokens: number | null
  open_reservations: number; unknown_entries: number; entries: number
}
export interface BudgetInput {
  token_limit: number; window_seconds: number; output_allowance: number; status: BudgetStatus
  expected_revision?: number
}

const LIMIT_KEYS = ['output_allowance', 'revision', 'status', 'token_limit', 'updated_at', 'window_seconds']
const SUMMARY_KEYS = ['agent_id', 'counted_tokens', 'entries', 'interval', 'limit', 'open_reservations', 'remaining_tokens', 'reported_tokens', 'snapshot', 'unknown_entries', 'window_seconds']
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join() === keys.join()

function isLimit(value: unknown): value is BudgetLimit {
  return record(value) && exact(value, LIMIT_KEYS)
    && count(value.token_limit) && (value.token_limit as number) > 0 && (value.token_limit as number) <= 1_000_000_000
    && count(value.output_allowance) && (value.output_allowance as number) > 0 && (value.output_allowance as number) <= 1_000_000 && (value.token_limit as number) > (value.output_allowance as number)
    && count(value.window_seconds) && (value.window_seconds as number) >= 3600
    && (value.window_seconds as number) <= 2_592_000
    && (value.status === 'active' || value.status === 'disabled')
    && Number.isSafeInteger(value.revision) && (value.revision as number) > 0
    && timestamp(value.updated_at)
}

// Payload extra, contagem negativa ou restante inventado são recusados como resposta inválida.
export function isBudgetSummary(value: unknown): value is BudgetSummary {
  if (!record(value) || !exact(value, SUMMARY_KEYS) || typeof value.agent_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.agent_id)) return false
  if (!isObservation(value.snapshot) || !isInterval(value.interval)) return false
  if (!['counted_tokens', 'reported_tokens', 'open_reservations', 'unknown_entries', 'entries', 'window_seconds'].every((key) => count(value[key]))) return false
  if ((value.window_seconds as number) < 3600 || (value.window_seconds as number) > 2_592_000 || (value.reported_tokens as number) > (value.counted_tokens as number)
    || (value.open_reservations as number) > (value.entries as number) || (value.unknown_entries as number) > (value.entries as number)) return false
  if (value.limit !== null && !isLimit(value.limit)) return false
  const active = value.limit !== null && (value.limit as BudgetLimit).status === 'active'
  if (active ? !count(value.remaining_tokens) : value.remaining_tokens !== null) return false
  if (active && value.remaining_tokens !== Math.max((value.limit as BudgetLimit).token_limit - (value.counted_tokens as number), 0)) return false
  return value.limit === null || (value.limit as BudgetLimit).window_seconds === value.window_seconds
}

const path = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/budget`

export async function getBudget(agentId: string, signal?: AbortSignal): Promise<BudgetSummary> {
  const value = await request(path(agentId), { authenticated: true, signal })
  if (!isBudgetSummary(value) || value.agent_id !== agentId) throw new ApiError('invalid_response')
  return value
}

export async function putBudget(agentId: string, body: BudgetInput, signal?: AbortSignal): Promise<BudgetSummary> {
  const value = await request(path(agentId), { body, method: 'PUT', authenticated: true, signal })
  // A gravação confirmada precisa avançar exatamente a revisão lida: nunca regredir.
  const expected = (body.expected_revision ?? 0) + 1
  if (!isBudgetSummary(value) || value.agent_id !== agentId || value.limit === null || value.limit.revision !== expected) throw new ApiError('invalid_response')
  return value
}

export function checkedBudgetSnapshot(incoming: BudgetSummary, previous: BudgetSummary | null): BudgetSummary {
  if (previous) {
    checkedObservation(incoming.snapshot, previous.snapshot)
    if (incoming.agent_id !== previous.agent_id || previous.limit && (!incoming.limit || incoming.limit.revision < previous.limit.revision
      || instant(incoming.limit.updated_at) < instant(previous.limit.updated_at)
      || incoming.limit.revision === previous.limit.revision && Object.keys(previous.limit).some((key) => incoming.limit![key as keyof BudgetLimit] !== previous.limit![key as keyof BudgetLimit]))) throw new ApiError('invalid_response')
  }
  // Totals and reservations are projections of current time; they can change at the same limit revision.
  return incoming
}
