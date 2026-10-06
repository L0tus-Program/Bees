import { ApiError, record, request } from './client'

export interface ToolManifest {
  manifest_version: 1
  plugin_key: string
  name: string
  version: string
  tools: { tool_name: string; version: string; executor: string }[]
}
export interface PluginRecord {
  id: string; manifest: ToolManifest; manifest_hash: string; enabled: boolean; revision: number; created_at: string; updated_at: string
}
export interface ToolRecord {
  plugin_id: string; plugin_name: string; plugin_version: string; plugin_enabled: boolean; plugin_revision: number
  tool_name: string; version: string; input_schema: Record<string, unknown>; output_schema: Record<string, unknown>; capabilities: string[]
  grant_id: string | null; grant_revision: number; granted: boolean; available: boolean
}
export interface ToolGrant {
  id: string; agent_id: string; plugin_id: string; tool_name: string; enabled: boolean; revision: number; created_at: string; updated_at: string
}
export interface Page<T> { items: T[]; has_more: boolean; next_offset: number | null }

const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0
const positive = (value: unknown): value is number => Number.isInteger(value) && (value as number) > 0
const date = (value: unknown) => typeof value === 'string' && !Number.isNaN(Date.parse(value))

export function isToolManifest(value: unknown): value is ToolManifest {
  return record(value) && value.manifest_version === 1 && ['plugin_key', 'name', 'version'].every((key) => text(value[key]))
    && Array.isArray(value.tools) && value.tools.length > 0 && value.tools.length <= 64
    && value.tools.every((tool) => record(tool) && ['tool_name', 'version', 'executor'].every((key) => text(tool[key])))
    && new Set(value.tools.map((tool: { tool_name: string }) => tool.tool_name)).size === value.tools.length
}
export function isPlugin(value: unknown): value is PluginRecord {
  return record(value) && text(value.id) && isToolManifest(value.manifest) && text(value.manifest_hash)
    && typeof value.enabled === 'boolean' && positive(value.revision) && date(value.created_at) && date(value.updated_at)
}
export function isTool(value: unknown): value is ToolRecord {
  if (!record(value)) return false
  return ['plugin_id', 'plugin_name', 'plugin_version', 'tool_name', 'version'].every((key) => text(value[key]))
    && ['plugin_enabled', 'granted', 'available'].every((key) => typeof value[key] === 'boolean')
    && positive(value.plugin_revision) && record(value.input_schema) && record(value.output_schema)
    && Array.isArray(value.capabilities) && value.capabilities.every(text) && new Set(value.capabilities).size === value.capabilities.length
    && (value.grant_id === null ? value.grant_revision === 0 && value.granted === false : text(value.grant_id) && positive(value.grant_revision))
    && (!value.available || value.plugin_enabled === true && value.granted === true)
}
function isGrant(value: unknown): value is ToolGrant {
  return record(value) && ['id', 'agent_id', 'plugin_id', 'tool_name'].every((key) => text(value[key]))
    && typeof value.enabled === 'boolean' && positive(value.revision) && date(value.created_at) && date(value.updated_at)
}
export function sameManifest(a: ToolManifest, b: ToolManifest) {
  return a.manifest_version === b.manifest_version && a.plugin_key === b.plugin_key && a.name === b.name && a.version === b.version
    && a.tools.length === b.tools.length && a.tools.every((tool, index) => tool.tool_name === b.tools[index].tool_name && tool.version === b.tools[index].version && tool.executor === b.tools[index].executor)
}
function checkedPage<T>(value: unknown, field: string, check: (entry: unknown) => entry is T, key: (entry: T) => string, offset: number): Page<T> {
  if (!record(value) || !Array.isArray(value[field]) || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  const items: T[] = []
  for (const item of value[field]) { if (!check(item)) throw new ApiError('invalid_response'); items.push(item) }
  if (items.length > 100 || new Set(items.map(key)).size !== items.length
    || (value.has_more ? items.length === 0 || value.next_offset !== offset + items.length : value.next_offset !== null)) throw new ApiError('invalid_response')
  return { items, has_more: value.has_more, next_offset: value.next_offset as number | null }
}
const path = (agent: string) => `/agents/${encodeURIComponent(agent)}/tools`
function offsetCheck(offset: number) { if (!Number.isInteger(offset) || offset < 0 || offset > 1_000_000) throw new ApiError('invalid_request') }
export async function getToolCatalog(signal?: AbortSignal): Promise<ToolManifest[]> {
  const value = await request('/plugins/catalog', { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.plugins) || !value.plugins.every(isToolManifest)
    || new Set(value.plugins.map((plugin) => plugin.plugin_key)).size !== value.plugins.length) throw new ApiError('invalid_response')
  return value.plugins
}
export async function getPlugins(signal?: AbortSignal, offset = 0): Promise<Page<PluginRecord>> {
  offsetCheck(offset)
  return checkedPage(await request(`/plugins?offset=${offset}&limit=100`, { authenticated: true, signal }), 'plugins', isPlugin, (entry) => entry.id, offset)
}
export async function getAgentTools(agentId: string, signal?: AbortSignal, offset = 0): Promise<Page<ToolRecord>> {
  offsetCheck(offset)
  return checkedPage(await request(`${path(agentId)}?offset=${offset}&limit=100`, { authenticated: true, signal }), 'tools', isTool, (entry) => `${entry.plugin_id}:${entry.tool_name}`, offset)
}
export async function installPlugin(manifest: ToolManifest, clientRequestId: string, signal?: AbortSignal): Promise<PluginRecord> {
  const value = await request('/plugins', { authenticated: true, signal, body: { manifest, client_request_id: clientRequestId } })
  if (!isPlugin(value) || !sameManifest(value.manifest, manifest)) throw new ApiError('invalid_response')
  // A replay may return an installation later enabled through another explicit operation.
  return value
}
export async function updatePlugin(plugin: PluginRecord, enabled: boolean, signal?: AbortSignal): Promise<PluginRecord> {
  const value = await request(`/plugins/${encodeURIComponent(plugin.id)}`, { method: 'PATCH', authenticated: true, signal, body: { expected_revision: plugin.revision, enabled } })
  if (!isPlugin(value) || value.id !== plugin.id || !sameManifest(value.manifest, plugin.manifest) || value.enabled !== enabled || value.revision < plugin.revision) throw new ApiError('invalid_response')
  return value
}
export async function updateToolGrant(agentId: string, tool: ToolRecord, enabled: boolean, signal?: AbortSignal): Promise<ToolGrant> {
  const value = await request(`${path(agentId)}/${encodeURIComponent(tool.plugin_id)}/${encodeURIComponent(tool.tool_name)}`, { method: 'PUT', authenticated: true, signal, body: { expected_revision: tool.grant_revision, enabled } })
  if (!isGrant(value) || value.agent_id !== agentId || value.plugin_id !== tool.plugin_id || value.tool_name !== tool.tool_name
    || value.enabled !== enabled || value.revision < tool.grant_revision || tool.grant_id !== null && value.id !== tool.grant_id) throw new ApiError('invalid_response')
  return value
}
