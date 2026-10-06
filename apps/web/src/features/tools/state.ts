import { ApiError, record } from '../../api/client'
import { getAgentTools, getPlugins, getToolCatalog, isToolManifest } from '../../api/tools'
import type { Page, PluginRecord, ToolManifest, ToolRecord } from '../../api/tools'

export interface ToolWorkspace { catalog: ToolManifest[]; plugins: PluginRecord[]; tools: ToolRecord[] }
export function toolKey(tool: Pick<ToolRecord, 'plugin_id' | 'tool_name'>) { return `${tool.plugin_id}:${tool.tool_name}` }
async function allPages<T>(read: (offset: number) => Promise<Page<T>>, key: (item: T) => string) {
  let offset = 0
  const entries = new Map<string, T>()
  while (offset <= 1_000_000) {
    const page = await read(offset)
    for (const item of page.items) {
      if (entries.has(key(item))) throw new ApiError('state_conflict')
      entries.set(key(item), item)
    }
    if (page.next_offset === null) return [...entries.values()]
    if (page.next_offset <= offset || page.next_offset > 1_000_000) throw new ApiError('invalid_response')
    offset = page.next_offset
  }
  throw new ApiError('invalid_response')
}
export async function loadTools(agentId: string, signal?: AbortSignal): Promise<ToolWorkspace> {
  const [catalog, plugins, tools] = await Promise.all([
    getToolCatalog(signal), allPages((offset) => getPlugins(signal, offset), (entry) => entry.id),
    allPages((offset) => getAgentTools(agentId, signal, offset), toolKey),
  ])
  for (const tool of tools) {
    const plugin = plugins.find((entry) => entry.id === tool.plugin_id)
    if (!plugin || plugin.revision !== tool.plugin_revision || plugin.enabled !== tool.plugin_enabled
      || !plugin.manifest.tools.some((entry) => entry.tool_name === tool.tool_name && entry.version === tool.version)) throw new ApiError('state_conflict')
  }
  for (const plugin of plugins) {
    if (plugin.manifest.tools.some((entry) => !tools.some((tool) => tool.plugin_id === plugin.id && tool.tool_name === entry.tool_name))) throw new ApiError('state_conflict')
  }
  return { catalog, plugins, tools }
}
export function parseManifest(value: string): ToolManifest {
  if (new TextEncoder().encode(value).byteLength > 64 * 1024) throw new ApiError('manifestSize')
  let manifest: unknown
  try { manifest = JSON.parse(value) } catch { throw new ApiError('manifestInvalid') }
  if (!record(manifest) || !isToolManifest(manifest)) throw new ApiError('manifestInvalid')
  return manifest
}
export function installationIdentity(create: () => string = () => crypto.randomUUID()) {
  let previous = ''; let id = ''
  return (manifest: ToolManifest) => {
    const signature = JSON.stringify(manifest)
    if (signature !== previous) { previous = signature; id = create() }
    return id
  }
}
export const uncertainMutation = (error: ApiError) => ['mutation_network', 'mutation_timeout', 'invalid_response'].includes(error.code)
