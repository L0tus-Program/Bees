import { ApiError, record, request } from './client'

export interface ConnectionIssue { kind: 'channel' | 'mcp' | 'computer' | 'executor' | 'webhook'; id: string; name: string; detail: string | null; code: string; blocking: boolean; since: string | null }
export async function getConnectionHealth(agentId: string, signal?: AbortSignal): Promise<ConnectionIssue[]> {
  const value = await request(`/agents/${encodeURIComponent(agentId)}/connections/health`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.issues) || !value.issues.every((item) => record(item) && typeof item.kind === 'string' && typeof item.code === 'string' && typeof item.name === 'string')) throw new ApiError('invalid_response')
  return value.issues as ConnectionIssue[]
}
