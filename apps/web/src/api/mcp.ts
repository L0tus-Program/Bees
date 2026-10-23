import { ApiError, record, request } from './client'

export interface McpServer {
  id: string; agent_id: string; name: string; url: string; has_token: boolean; enabled: boolean
  status: 'ready' | 'failed' | 'removed'; reason_code: string | null; server_name: string | null
  tools: { name: string; description: string; read_only: boolean }[]; refreshed_at: string | null; revision: number
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/mcp-servers`
function isServer(value: unknown): value is McpServer {
  return record(value) && ['id', 'name', 'url', 'status'].every((key) => typeof value[key] === 'string')
    && Array.isArray(value.tools) && Number.isSafeInteger(value.revision) && typeof value.enabled === 'boolean'
}
export async function listMcpServers(agentId: string, signal?: AbortSignal): Promise<McpServer[]> {
  const value = await request(base(agentId), { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.servers) || !value.servers.every(isServer)) throw new ApiError('invalid_response')
  return value.servers
}
export async function createMcpServer(agentId: string, body: { client_request_id: string; name: string; url: string; token?: string }) {
  const value = await request(base(agentId), { authenticated: true, body, timeoutMs: 40000 })
  if (!isServer(value)) throw new ApiError('invalid_response')
  return value
}
async function post(server: McpServer, action: string, body: Record<string, unknown> = {}) {
  const value = await request(`${base(server.agent_id)}/${encodeURIComponent(server.id)}/${action}`, { authenticated: true, body: { expected_revision: server.revision, ...body }, timeoutMs: 40000 })
  if (!isServer(value)) throw new ApiError('invalid_response')
  return value
}
export const refreshMcpServer = (server: McpServer) => post(server, 'refresh')
export const setMcpServerEnabled = (server: McpServer, enabled: boolean) => post(server, 'enabled', { enabled })
export const removeMcpServer = (server: McpServer) => post(server, 'remove')
