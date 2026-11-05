import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { enableIntent, sendSkill } from './skills'
import { setCsrfToken } from './client'
import { skill } from '../features/skills/fixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
const otherId = '33333333-3333-4333-8333-333333333333'

describe.each([true, false])('skill enabled=%s receipt binding', (enabled) => {
  const previous = { ...skill, revision: 5, enabled_version: enabled ? null : 1 }
  const receipt = { ...previous, revision: 6, enabled_version: enabled ? 1 : null }
  it('accepts the confirmed state and deterministic replay', async () => {
    const fetcher = vi.fn(async () => Response.json(receipt)); vi.stubGlobal('fetch', fetcher)
    const intent = enableIntent(previous)
    expect(await sendSkill(intent)).toEqual(receipt)
    expect(await sendSkill(intent)).toEqual(receipt)
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it.each([
    ['stale revision', { revision: 5 }],
    ['skipped revision', { revision: 7 }],
    ['opposite state', { enabled_version: enabled ? null : 1 }],
    ['another version', { current_version: 2, enabled_version: enabled ? 2 : null, current: { ...skill.current, version: 2 } }],
  ])('rejects %s without retry', async (_label, change) => {
    const fetcher = vi.fn(async () => Response.json({ ...receipt, ...change })); vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(enableIntent(previous))).rejects.toThrow('invalid_response')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each(['agentId', 'skillId', 'revision', 'version', 'enabled', 'body'] as const)('keeps the original command when %s changes during POST', async (field) => {
    const intent = enableIntent(previous)
    let finish!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve })))
    const result = sendSkill(intent)
    if (field === 'agentId' || field === 'skillId') intent[field] = otherId
    else if (field === 'body') intent.body = { ...intent.body, expected_revision: 7, version: 2, enabled: !enabled }
    else if (field === 'revision') intent.body.expected_revision = 7
    else if (field === 'version') intent.body.version = 2
    else intent.body.enabled = !enabled
    finish(Response.json(receipt))
    expect(await result).toEqual(receipt)
  })
  it('rejects a receipt matching the mutated command instead of the sent command', async () => {
    const intent = enableIntent(previous)
    let finish!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { finish = resolve })))
    const result = sendSkill(intent)
    intent.body.expected_revision = 7; intent.body.version = 2; intent.body.enabled = !enabled
    finish(Response.json({ ...receipt, revision: 8, current_version: 2, enabled_version: enabled ? null : 2, current: { ...skill.current, version: 2 } }))
    await expect(result).rejects.toThrow('invalid_response')
  })
})

describe('enable command validation', () => {
  it.each([
    (intent: ReturnType<typeof enableIntent>) => { intent.body.expected_revision = 0 },
    (intent: ReturnType<typeof enableIntent>) => { intent.body.expected_revision = Number.MAX_SAFE_INTEGER },
    (intent: ReturnType<typeof enableIntent>) => { intent.body.version = 0 },
    (intent: ReturnType<typeof enableIntent>) => { intent.body.version = 2 },
    (intent: ReturnType<typeof enableIntent>) => { intent.body.enabled = 'true' },
    (intent: ReturnType<typeof enableIntent>) => { intent.body.extra = true },
    (intent: ReturnType<typeof enableIntent>) => { intent.skillId = undefined },
    (intent: ReturnType<typeof enableIntent>) => { intent.path = `/agents/${skill.agent_id}/skills/${otherId}/enabled` },
    (intent: ReturnType<typeof enableIntent>) => { intent.method = undefined },
  ])('rejects malformed enable commands before fetch %#', async (alter) => {
    const intent = enableIntent(skill); alter(intent)
    const fetcher = vi.fn(async () => Response.json({ ...skill, revision: 2, enabled_version: 1 })); vi.stubGlobal('fetch', fetcher)
    await expect(sendSkill(intent)).rejects.toThrow('invalid_request')
    expect(fetcher).not.toHaveBeenCalled()
  })
})
