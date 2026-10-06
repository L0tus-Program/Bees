import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { getAgentTools, getPlugins, getToolCatalog, installPlugin, updatePlugin, updateToolGrant } from './tools'
import type { PluginRecord, ToolManifest, ToolRecord } from './tools'
import { installationIdentity, loadTools, parseManifest } from '../features/tools/state'

const stamp = '2026-10-06T18:00:00Z'
const manifest: ToolManifest = { manifest_version: 1, plugin_key: 'bees.text', name: 'Texto local', version: '1.0.0', tools: [{ tool_name: 'text.normalize', version: '1', executor: 'builtin:text.normalize@1' }] }
const plugin: PluginRecord = { id: 'plugin-1', manifest, manifest_hash: 'hash', enabled: false, revision: 1, created_at: stamp, updated_at: stamp }
const tool: ToolRecord = { plugin_id: plugin.id, plugin_name: manifest.name, plugin_version: manifest.version, plugin_enabled: false, plugin_revision: 1, tool_name: 'text.normalize', version: '1', capabilities: ['pure_text'], input_schema: { type: 'object' }, output_schema: { type: 'object' }, grant_id: null, grant_revision: 0, granted: false, available: false }
const grant = { id: 'grant-1', agent_id: 'bee-1', plugin_id: plugin.id, tool_name: tool.tool_name, enabled: true, revision: 1, created_at: stamp, updated_at: stamp }
const page = (field: 'plugins' | 'tools', entries: unknown[]) => ({ [field]: entries, has_more: false, next_offset: null })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('tool management API boundaries', () => {
  it('loads the server catalog and read-only tool grants without installing or granting', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ plugins: [manifest] })).mockResolvedValueOnce(Response.json(page('tools', [tool])))
    vi.stubGlobal('fetch', fetcher)
    expect(await getToolCatalog()).toEqual([manifest])
    expect((await getAgentTools('bee-1')).items[0].granted).toBe(false)
    expect(fetcher.mock.calls.every((call) => call[1].method === 'GET' && call[1].body === undefined && call[1].credentials === 'same-origin')).toBe(true)
  })
  it('installs the exact server manifest with CSRF and a stable request ID', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(plugin)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await installPlugin(manifest, 'request-1')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', headers: { 'X-Bees-CSRF': 'csrf-test' } })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ manifest, client_request_id: 'request-1' })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).not.toHaveProperty('enabled')
  })
  it('updates global status with CAS and confirms the returned state', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...plugin, enabled: true, revision: 2 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    expect((await updatePlugin(plugin, true)).enabled).toBe(true)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ expected_revision: 1, enabled: true }) })
  })
  it('grants a tool to only the selected bee using an explicit CAS PUT', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(grant)); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await updateToolGrant('bee-1', tool, true)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/tools/plugin-1/text.normalize')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'PUT', body: JSON.stringify({ expected_revision: 0, enabled: true }) })
  })
  it('does not send any management mutation without session CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(installPlugin(manifest, 'request-1')).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(updatePlugin(plugin, true)).rejects.toMatchObject({ code: 'csrf_missing' })
    await expect(updateToolGrant('bee-1', tool, true)).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('never automatically retries conflicting or uncertain changes', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ error: { code: 'tool_conflict' } }, { status: 409 })).mockRejectedValueOnce(new TypeError('Network lost'))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(updatePlugin(plugin, true)).rejects.toMatchObject({ code: 'tool_conflict', status: 409 })
    await expect(updateToolGrant('bee-1', tool, true)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })
  it.each([{ ...grant, agent_id: 'other' }, { ...grant, plugin_id: 'other' }, { ...grant, tool_name: 'shell' }, { ...grant, enabled: false }, { ...grant, revision: 0 }])('rejects a grant result that misstates or redirects the choice: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf-test')
    await expect(updateToolGrant('bee-1', tool, true)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([{ ...plugin, id: 'other', enabled: true }, { ...plugin, enabled: false }, { ...plugin, enabled: true, revision: 0 }, { ...plugin, enabled: true, manifest: { ...manifest, tools: [{ ...manifest.tools[0], executor: 'unknown' }] } }])('rejects a global mutation result that differs from its intent: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value))); setCsrfToken('csrf-test')
    await expect(updatePlugin(plugin, true)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects installation returning a different manifest', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...plugin, manifest: { ...manifest, plugin_key: 'other' } }))); setCsrfToken('csrf-test')
    await expect(installPlugin(manifest, 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { tools: [tool, tool], has_more: false, next_offset: null },
    { tools: [tool], has_more: true, next_offset: 0 }, { tools: [], has_more: true, next_offset: 100 },
    { tools: [{ ...tool, granted: true }], has_more: false, next_offset: null },
    { tools: [{ ...tool, available: true }], has_more: false, next_offset: null },
    { tools: [{ ...tool, input_schema: [] }], has_more: false, next_offset: null },
  ])('rejects malformed or unsafe pages: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getAgentTools('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects duplicate catalog entries and invalid offsets', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ plugins: [manifest, manifest] })); vi.stubGlobal('fetch', fetcher)
    await expect(getToolCatalog()).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getPlugins(undefined, -1)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(getAgentTools('bee-1', undefined, 1_000_001)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('tool management state', () => {
  it('loads all pages of installations and accesses without any mutation', async () => {
    const second = { ...plugin, id: 'plugin-2' }; const secondTool = { ...tool, plugin_id: second.id }
    const fetcher = vi.fn<(url: string, options: RequestInit) => Promise<Response>>((url) => {
      const value = url.endsWith('/catalog') ? { plugins: [manifest] }
        : url.includes('/agents/') ? page('tools', [tool, secondTool])
          : url.includes('offset=0') ? { plugins: [plugin], has_more: true, next_offset: 1 } : page('plugins', [second])
      return Promise.resolve(Response.json(value))
    })
    vi.stubGlobal('fetch', fetcher)
    expect(await loadTools('bee-1')).toMatchObject({ plugins: [plugin, second], tools: [tool, secondTool] })
    expect(fetcher.mock.calls.every((call) => call[1].method === 'GET')).toBe(true)
    expect(fetcher.mock.calls.map((call) => call[0])).toContain('/api/v1/plugins?offset=1&limit=100')
  })
  it('refuses a workspace missing its tool access rows instead of implying access from installation', async () => {
    const fetcher = vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith('/catalog') ? { plugins: [manifest] } : url.includes('/agents/') ? page('tools', []) : page('plugins', [plugin]))))
    vi.stubGlobal('fetch', fetcher)
    await expect(loadTools('bee-1')).rejects.toMatchObject({ code: 'state_conflict' })
  })
  it('loads the complete workspace and catches concurrent mismatches before showing mutable controls', async () => {
    const fetcher = vi.fn((url: string) => Promise.resolve(Response.json(url.endsWith('/catalog') ? { plugins: [manifest] } : url.includes('/agents/') ? page('tools', [tool]) : page('plugins', [{ ...plugin, revision: 2 }]))))
    vi.stubGlobal('fetch', fetcher)
    await expect(loadTools('bee-1')).rejects.toMatchObject({ code: 'state_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(3)
  })
  it('preserves an unknown manifest field for server validation instead of treating it as authority', () => {
    const input = { ...manifest, permissions: 'allow everything', tools: [{ ...manifest.tools[0], identity: 'admin' }] }
    expect(parseManifest(JSON.stringify(input))).toEqual(input)
  })
  it.each(['null', '[]', '{', '{"manifest_version":1}', JSON.stringify({ ...manifest, tools: [manifest.tools[0], manifest.tools[0]] })])('rejects invalid manifest input: %s', (input) => {
    expect(() => parseManifest(input)).toThrow('manifestInvalid')
  })
  it('bounds encoded manifest size before parsing', () => {
    expect(() => parseManifest('á'.repeat(33000))).toThrow('manifestSize')
  })
  it('preserves installation request identity across explicit retries and changes it only for changed input', () => {
    let count = 0; const id = installationIdentity(() => `request-${++count}`)
    expect(id(manifest)).toBe('request-1'); expect(id(manifest)).toBe('request-1')
    expect(id({ ...manifest, name: 'Outro' })).toBe('request-2')
  })
})
