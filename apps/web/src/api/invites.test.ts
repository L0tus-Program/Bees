import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { createInvite } from './computers'

const invite = { invite_id: '238f792a-e4ca-466c-80f9-a8222041eb53', token: 'a'.repeat(43), expires_at: '2026-11-04T10:00:00+00:00', delivered_locally: false }
beforeEach(() => setCsrfToken('csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function response(value: unknown) {
  const fetcher = vi.fn(async () => Response.json(value))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}
it.each([
  null, [], {},
  ...[{ invite_id: undefined }, { invite_id: null }, { invite_id: '' }, { invite_id: 'not-a-uuid' },
    { token: undefined }, { token: null }, { token: '' }, { token: ' ' }, { token: 'a'.repeat(42) }, { token: 'a'.repeat(44) },
    { token: 'a'.repeat(42) + ';' }, { token: 'a'.repeat(42) + '\n' }, { token: '$(echo unsafe)' },
    { expires_at: undefined }, { expires_at: null }, { expires_at: '' }, { expires_at: 'tomorrow' },
    { expires_at: '2026-11-04' }, { expires_at: '2026-11-04T10:00:00' }, { expires_at: '2026-13-04T10:00:00Z' },
    { delivered_locally: undefined }, { delivered_locally: null }, { delivered_locally: 'false' }, { delivered_locally: 0 },
  ].map((patch) => ({ ...invite, ...patch })),
])('rejects malformed invite receipts without repeating issuance: %j', async (value) => {
  const fetcher = response(value)
  await expect(createInvite('personal', 'Meu computador')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('rejects local delivery for a personal connector', async () => {
  const fetcher = response({ ...invite, delivered_locally: true })
  await expect(createInvite('personal', 'Meu computador')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each([false, true])('accepts runtime delivery mode %s', async (delivered_locally) => {
  const receipt = { ...invite, delivered_locally, token: '_-'.repeat(21) + 'A', expires_at: '2026-11-04T10:00:00.123456Z' }
  response(receipt)
  await expect(createInvite('computer_runtime', 'Runtime')).resolves.toEqual(receipt)
})
it('accepts a personal invite and preserves authentication and request intent', async () => {
  const fetcher = response(invite)
  await expect(createInvite('personal', 'Meu computador')).resolves.toEqual(invite)
  expect(fetcher).toHaveBeenCalledWith('/api/v1/executors/invites', expect.objectContaining({ method: 'POST', credentials: 'same-origin', redirect: 'error', headers: expect.objectContaining({ 'X-Bees-CSRF': 'csrf' }), body: JSON.stringify({ kind: 'personal', name: 'Meu computador' }) }))
})
