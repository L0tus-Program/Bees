import type { ArtifactRecord } from './api'
export const agentId = '11111111-1111-4111-8111-111111111111'
export const taskId = '22222222-2222-4222-8222-222222222222'
export const artifact: ArtifactRecord = {
  id: '33333333-3333-4333-8333-333333333333', task_id: taskId, run_id: '44444444-4444-4444-8444-444444444444',
  name: 'relatório.txt', media_type: 'text/plain', size_bytes: 3, sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  version: 1, series_id: '33333333-3333-4333-8333-333333333333', previous_id: null, status: 'ready', revision: 2,
  created_at: '2026-10-12T12:00:00+00:00', updated_at: '2026-10-12T12:00:01+00:00',
}
export const page = (items: unknown[] = [artifact], hasMore = false, nextOffset: number | null = null) => ({ artifacts: items, has_more: hasMore, next_offset: nextOffset })
export function binary(content = 'abc', overrides: Record<string, string> = {}, status = 200) {
  return new Response(content, { status, headers: {
    'Content-Disposition': `attachment; filename="relat_rio.txt"; filename*=UTF-8''relat%C3%B3rio.txt`,
    'Content-Type': 'text/plain; charset=utf-8', 'Content-Length': '3', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store', ...overrides,
  } })
}
