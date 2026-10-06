import { ApiError } from '../../api/client'
import { getEnvironmentCatalog, getEnvironments } from '../../api/environments'
import type { EnvironmentCatalog, EnvironmentPlan, EnvironmentRecord, EnvironmentTemplate, Resources } from '../../api/environments'

export interface EnvironmentWorkspace extends EnvironmentCatalog { environments: EnvironmentRecord[] }
export async function loadEnvironments(agent: string, signal?: AbortSignal): Promise<EnvironmentWorkspace> {
  const [catalog, environments] = await Promise.all([getEnvironmentCatalog(signal), allEnvironments(agent, signal)])
  return { ...catalog, environments }
}
async function allEnvironments(agent: string, signal?: AbortSignal) {
  const items: EnvironmentRecord[] = []; const seen = new Set<string>(); let offset = 0
  while (offset <= 1_000_000) {
    const page = await getEnvironments(agent, signal, offset)
    for (const item of page.items) {
      if (seen.has(item.id)) throw new ApiError('state_conflict')
      seen.add(item.id); items.push(item)
    }
    if (page.next_offset === null) return items
    if (page.next_offset <= offset) throw new ApiError('invalid_response')
    offset = page.next_offset
  }
  throw new ApiError('invalid_response')
}
export function resourceProfile(template: EnvironmentTemplate, expanded: boolean): Resources {
  if (!expanded) return { ...template.defaults }
  const cap = (key: keyof Resources, desired: number) => Math.min(template.limits[key].max, Math.max(template.limits[key].min, desired))
  return { cpu_count: cap('cpu_count', 4), memory_mib: cap('memory_mib', 8192), disk_gib: cap('disk_gib', 60) }
}
export function validPlan(plan: EnvironmentPlan, template: EnvironmentTemplate) {
  return plan.name.trim().length > 0 && plan.name.trim().length <= 100 && ![...plan.name].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127) && plan.template_id === template.id
    && (['cpu_count', 'memory_mib', 'disk_gib'] as const).every((key) => Number.isInteger(plan[key]) && plan[key] >= template.limits[key].min && plan[key] <= template.limits[key].max)
}
export function planIdentity(create: () => string = () => crypto.randomUUID()) {
  let signature = ''; let id = ''
  return (plan: EnvironmentPlan) => {
    const next = JSON.stringify([plan.name, plan.template_id, plan.cpu_count, plan.memory_mib, plan.disk_gib])
    if (next !== signature) { signature = next; id = create() }
    return id
  }
}
export const uncertainEnvironmentMutation = (error: ApiError) => error.status >= 500 || ['mutation_network', 'mutation_timeout', 'invalid_response'].includes(error.code)
