import type { EnvironmentRecord } from '../../api/environments'
import type { ProvisioningPlan, ProvisioningReviewState } from '../../api/provisioning'

export function authorizationExpired(plan: ProvisioningPlan, now = Date.now()) {
  return plan.reason_code === 'authorization_expired' || plan.authorization_expires_at !== null && Date.parse(plan.authorization_expires_at) <= now
}
export function canAuthorizePlan(plan: ProvisioningPlan, state: ProvisioningReviewState, environment: EnvironmentRecord, now = Date.now()) {
  return plan.context_valid && environment.status === 'awaiting_host' && plan.plan.environment_revision === environment.revision
    && plan.plan.cpu_count === environment.cpu_count && plan.plan.memory_bytes === environment.memory_mib * 1024 ** 2 && plan.plan.disk_bytes === environment.disk_gib * 1024 ** 3
    && state.host?.host_id === plan.plan.host_id && state.host.revision === plan.plan.host_revision
    && (plan.status === 'prepared' || plan.status === 'authorized' && authorizationExpired(plan, now))
}
export function canPreparePlan(state: ProvisioningReviewState, environment: EnvironmentRecord) {
  return environment.status === 'awaiting_host' && state.preparation_available && state.host !== null && (state.plan === null || state.plan.status === 'revoked')
}
export function canRevokePlan(plan: ProvisioningPlan) { return plan.status !== 'revoked' }
export function reviewKey(plan: ProvisioningPlan) {
  return JSON.stringify([plan.plan_id, plan.plan_hash, plan.revision, plan.status, plan.context_valid, plan.reason_code, plan.authorization_expires_at])
}
export function provisioningAttemptIds(create: () => string = () => crypto.randomUUID()) {
  const attempts = new Map<string, string>()
  return (signature: string) => {
    let id = attempts.get(signature)
    if (!id) { id = create(); attempts.set(signature, id) }
    return id
  }
}
