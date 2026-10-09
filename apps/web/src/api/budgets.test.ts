import { afterEach, describe, expect, it, vi } from 'vitest'
import { getBudget, isBudgetSummary, putBudget } from './budgets'
import { setCsrfToken } from './client'

const limit = { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', revision: 1, updated_at: '2026-10-09T18:00:00+00:00' }
const summary = { limit, window_seconds: 86400, counted_tokens: 150, reported_tokens: 150, remaining_tokens: 9850, open_reservations: 0, unknown_entries: 0, entries: 1 }
const respond = (body: unknown, status = 200) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => { vi.restoreAllMocks(); setCsrfToken() })

describe('budget contract', () => {
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
    expect(isBudgetSummary({ ...summary, window_seconds: 3600 })).toBe(false)
  })
  it('reads and refuses invalid responses as a contract error', async () => {
    respond(summary)
    await expect(getBudget('bee 1')).resolves.toEqual(summary)
    vi.restoreAllMocks(); respond({ ...summary, extra: true })
    await expect(getBudget('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('requires the saved revision to advance exactly once', async () => {
    setCsrfToken('csrf')
    const fetch = respond({ ...summary, limit: { ...limit, revision: 3 } })
    await expect(putBudget('bee-1', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 2 })).resolves.toMatchObject({ limit: { revision: 3 } })
    const [, init] = fetch.mock.calls[0]
    expect(init?.method).toBe('PUT')
    expect(JSON.parse(String(init?.body))).toMatchObject({ expected_revision: 2 })
    vi.restoreAllMocks(); respond({ ...summary, limit: { ...limit, revision: 2 } })
    await expect(putBudget('bee-1', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 2 })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('keeps a stale revision conflict as a 409 code', async () => {
    setCsrfToken('csrf')
    respond({ error: { code: 'state_conflict', message: 'x' } }, 409)
    await expect(putBudget('bee-1', { token_limit: 10000, window_seconds: 86400, output_allowance: 4096, status: 'active', expected_revision: 1 })).rejects.toMatchObject({ code: 'state_conflict', status: 409 })
  })
})
