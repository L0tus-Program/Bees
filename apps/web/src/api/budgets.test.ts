import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkedBudgetSnapshot, getBudget, isBudgetSummary, putBudget } from './budgets'
import type { BudgetSummary } from './budgets'
import { setCsrfToken } from './client'

const limit = { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active' as const, revision: 1, updated_at: '2026-10-09T18:00:00+00:00' }
const summary = { agent_id: '11111111-1111-4111-8111-111111111111', snapshot: { event_seq: 1, observed_at: '2026-10-12T12:00:00Z' }, interval: { since: '2026-10-11T12:00:00Z', until: '2026-10-12T12:00:00Z' }, limit, window_seconds: 86400, counted_tokens: 150, reported_tokens: 150, remaining_tokens: 9850, open_reservations: 0, unknown_entries: 0, entries: 1 }
const respond = (body: unknown, status = 200) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => { vi.restoreAllMocks(); setCsrfToken() })

describe('budget contract', () => {
  it('requires binding to the requested bee', async () => {
    respond(summary)
    await expect(getBudget('22222222-2222-4222-8222-222222222222')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('refuses regressive or rewritten limits, and accepts time-derived totals at the same revision', () => {
    const previous = summary as BudgetSummary
    expect(checkedBudgetSnapshot({ ...previous, counted_tokens: 200, remaining_tokens: 9800, snapshot: { ...previous.snapshot, observed_at: '2026-10-12T12:01:00Z' } }, previous).counted_tokens).toBe(200)
    for (const change of [{ revision: 0 }, { token_limit: 20000 }, { status: 'disabled' as const }, { updated_at: '2026-10-09T17:00:00Z' }]) expect(() => checkedBudgetSnapshot({ ...previous, limit: { ...limit, ...change } }, previous)).toThrow('invalid_response')
    expect(() => checkedBudgetSnapshot({ ...previous, limit: null, remaining_tokens: null }, previous)).toThrow('invalid_response')
    expect(() => checkedBudgetSnapshot({ ...previous, snapshot: { ...previous.snapshot, event_seq: 0 } }, previous)).toThrow('invalid_response')
  })
  it('accepts the exact summary and an unlimited bee', () => {
    expect(isBudgetSummary(summary)).toBe(true)
    expect(isBudgetSummary({ ...summary, limit: null, remaining_tokens: null })).toBe(true)
    expect(isBudgetSummary({ ...summary, limit: { ...limit, status: 'disabled' }, remaining_tokens: null })).toBe(true)
  })
  it('rejects extra fields, negative counts and invented remaining budget', () => {
    expect(isBudgetSummary({ ...summary, cost_usd: 1 })).toBe(false)
    expect(isBudgetSummary({ ...summary, limit: { ...limit, secret: 'x' } })).toBe(false)
    expect(isBudgetSummary({ ...summary, counted_tokens: -1 })).toBe(false)
    expect(isBudgetSummary({ ...summary, unknown_entries: 1.5 })).toBe(false)
    expect(isBudgetSummary({ ...summary, limit: null, remaining_tokens: 500 })).toBe(false)
    expect(isBudgetSummary({ ...summary, remaining_tokens: null })).toBe(false)
    expect(isBudgetSummary({ ...summary, remaining_tokens: 10000 })).toBe(false)
    expect(isBudgetSummary({ ...summary, reported_tokens: 151 })).toBe(false)
    expect(isBudgetSummary({ ...summary, limit: { ...limit, updated_at: '2026-10-09T18:00:00' } })).toBe(false)
    expect(isBudgetSummary({ ...summary, window_seconds: 3600 })).toBe(false)
  })
  it('reads and refuses invalid responses as a contract error', async () => {
    respond(summary)
    await expect(getBudget('11111111-1111-4111-8111-111111111111')).resolves.toEqual(summary)
    vi.restoreAllMocks(); respond({ ...summary, extra: true })
    await expect(getBudget('11111111-1111-4111-8111-111111111111')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('requires the saved revision to advance exactly once', async () => {
    setCsrfToken('csrf')
    const fetch = respond({ ...summary, limit: { ...limit, revision: 3 } })
    await expect(putBudget('11111111-1111-4111-8111-111111111111', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 2 })).resolves.toMatchObject({ limit: { revision: 3 } })
    const [, init] = fetch.mock.calls[0]
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toMatchObject({ expected_revision: 2 })
    vi.restoreAllMocks(); respond({ ...summary, limit: { ...limit, revision: 2 } })
    await expect(putBudget('11111111-1111-4111-8111-111111111111', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 2 })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('keeps a stale revision conflict as a 409 code', async () => {
    setCsrfToken('csrf')
    respond({ error: { code: 'state_conflict', message: 'x' } }, 409)
    await expect(putBudget('11111111-1111-4111-8111-111111111111', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 1 })).rejects.toMatchObject({ code: 'state_conflict', status: 409 })
  })
})
