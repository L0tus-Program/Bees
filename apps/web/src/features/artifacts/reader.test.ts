import { afterEach, describe, expect, it, vi } from 'vitest'
import { ArtifactReader } from './reader'
import { agentId, artifact, page, taskId } from './fixtures'

afterEach(() => vi.unstubAllGlobals())
describe('task-scoped artifact reader', () => {
  it('preserves earlier pages on pagination failure and restarts the list only after a successful refresh', async () => {
    const items = Array.from({ length: 100 }, (_, index) => ({ ...artifact, id: `33333333-3333-4333-8333-${index.toString().padStart(12, '0')}` }))
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(page(items, true, 100)))
      .mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce(Response.json(page([artifact])))
    vi.stubGlobal('fetch', fetcher)
    const reader = new ArtifactReader(agentId, taskId, vi.fn())
    await reader.load(); expect(reader.state.page?.items).toHaveLength(100)
    await reader.load(100); expect(reader.state.page?.items).toHaveLength(100); expect(reader.state.error?.code).toBe('network_error')
    await reader.load(); expect(reader.state.page?.items).toEqual([artifact]); expect(reader.state.error).toBeNull()
  })
  it('appends exact versions, with a separate ID for each version', async () => {
    const items = Array.from({ length: 100 }, (_, index) => ({ ...artifact, id: `33333333-3333-4333-8333-${index.toString().padStart(12, '0')}` }))
    const version = { ...artifact, id: '55555555-5555-4555-8555-555555555555', version: 2, previous_id: artifact.id }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(Response.json(page(items, true, 100))).mockResolvedValueOnce(Response.json(page([version]))))
    const reader = new ArtifactReader(agentId, taskId, vi.fn())
    await reader.load(); await reader.load(100)
    expect(reader.state.page?.items).toHaveLength(101); expect(reader.state.page?.items.at(-1)).toEqual(version)
  })
  it.each([{ revision: 1 }, { status: 'draft', revision: 3 }, { name: 'altered.txt', revision: 3 }, { sha256: 'a'.repeat(64), revision: 3 }])('keeps verified metadata after regressive or rewritten replies %j', async (change) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(Response.json(page())).mockResolvedValueOnce(Response.json(page([{ ...artifact, ...change }]))))
    const reader = new ArtifactReader(agentId, taskId, vi.fn()); await reader.load(); await reader.load()
    expect(reader.state.page?.items).toEqual([artifact]); expect(reader.state.error?.code).toBe('invalid_response')
  })
  it('aborts on leaving the task and ignores a transport that returns late anyway', async () => {
    let resolve!: (value: Response) => void
    const fetcher = vi.fn().mockImplementation(() => new Promise<Response>((result) => { resolve = result })); vi.stubGlobal('fetch', fetcher)
    const publish = vi.fn(); const reader = new ArtifactReader(agentId, taskId, publish)
    const pending = reader.load(); reader.stop()
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
    resolve(Response.json(page())); await pending
    expect(reader.state.page).toBeNull(); expect(publish).toHaveBeenCalledTimes(1)
    await reader.load(); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('fences an earlier refresh after a newer request completes', async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal('fetch', vi.fn().mockImplementationOnce(() => new Promise<Response>((result) => { resolve = result })).mockResolvedValueOnce(Response.json(page([]))))
    const reader = new ArtifactReader(agentId, taskId, vi.fn())
    const old = reader.load(); await reader.load(); resolve(Response.json(page())); await old
    expect(reader.state.page?.items).toEqual([])
  })
})
