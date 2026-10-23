import { ApiError, record, request } from './client'

export type ChannelKind = 'telegram' | 'discord'
export type ChannelMode = 'chat' | 'agent' | 'research'
export interface Channel {
  id: string; agent_id: string; conversation_id: string; kind: ChannelKind; name: string
  status: 'pending' | 'active' | 'paused' | 'revoked' | 'failed'; mode: ChannelMode; target: string | null
  paired: boolean; peer_name: string | null; bot_username: string | null; pairing_expires_at: string | null
  reason_code: string | null; last_activity_at: string | null; revision: number; created_at: string; updated_at: string
}
export interface Pairing { channel: Channel; pairing_code: string | null; pairing_link: string | null; replayed?: boolean }

const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/channels`
function isChannel(value: unknown): value is Channel {
  return record(value) && ['id', 'kind', 'name', 'status', 'mode'].every((key) => typeof value[key] === 'string')
    && Number.isSafeInteger(value.revision) && typeof value.paired === 'boolean'
}
function isPairing(value: unknown): value is Pairing {
  return record(value) && isChannel(value.channel) && (value.pairing_code === null || typeof value.pairing_code === 'string')
}

export async function listChannels(agentId: string, signal?: AbortSignal): Promise<Channel[]> {
  const value = await request(base(agentId), { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.channels) || !value.channels.every(isChannel)) throw new ApiError('invalid_response')
  return value.channels
}
export async function createChannel(agentId: string, body: { client_request_id: string; kind: ChannelKind; token: string; target?: string; name?: string; mode: ChannelMode }): Promise<Pairing> {
  const value = await request(base(agentId), { authenticated: true, body, timeoutMs: 20000 })
  if (!isPairing(value)) throw new ApiError('invalid_response')
  return value
}
export async function updateChannel(channel: Channel, change: { mode?: ChannelMode; paused?: boolean }): Promise<Channel> {
  const value = await request(`${base(channel.agent_id)}/${encodeURIComponent(channel.id)}/update`, { authenticated: true, body: { expected_revision: channel.revision, ...change } })
  if (!isChannel(value)) throw new ApiError('invalid_response')
  return value
}
export async function newPairingCode(channel: Channel): Promise<Pairing> {
  const value = await request(`${base(channel.agent_id)}/${encodeURIComponent(channel.id)}/code`, { authenticated: true, body: { expected_revision: channel.revision } })
  if (!isPairing(value)) throw new ApiError('invalid_response')
  return value
}
export async function revokeChannel(channel: Channel): Promise<Channel> {
  const value = await request(`${base(channel.agent_id)}/${encodeURIComponent(channel.id)}/revoke`, { authenticated: true, body: { expected_revision: channel.revision } })
  if (!isChannel(value)) throw new ApiError('invalid_response')
  return value
}
