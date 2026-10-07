import type { EnvironmentRecord } from './environments'
import type { ProvisioningHost, ProvisioningPlan } from './provisioning'

export const agentId = 'e8a4f541-6378-4dc8-b41e-960b4ab5f52c'
export const environment: EnvironmentRecord = { id: '031689bb-20f2-47ae-8b0c-4a49e03c58c3', agent_id: agentId, name: 'Computador da Aurora', template_id: 'linux-desktop-v1', status: 'awaiting_host', revision: 1,
  cpu_count: 2, memory_mib: 4096, disk_gib: 30, created_at: '2026-10-07T18:00:00Z', updated_at: '2026-10-07T18:00:00Z', reason_code: 'host_setup_required', usable: false }
export const host: ProvisioningHost = { host_id: 'b7627217-9c14-4aaf-a12f-91f08d917cfa', revision: 2, fingerprint: 'ABCD-EF01-2345-6789' }
export const plan: ProvisioningPlan = { plan_id: 'be4a1f4d-7291-4cfd-b623-b615047b9b22', plan_hash: 'a'.repeat(64), revision: 1, status: 'prepared', created_at: '2026-10-07T18:00:00Z', authorization_expires_at: null,
  usable: false, context_valid: true, reason_code: null, plan: { format: 1, plan_id: 'be4a1f4d-7291-4cfd-b623-b615047b9b22', installation_id: 'f586fe68-60a4-4cbb-a504-5961b709d764', agent_id: agentId, host_id: host.host_id,
    host_revision: host.revision, environment_id: environment.id, environment_revision: environment.revision, job_id: '51851ef8-7e4b-433a-80ba-fe81a8033ba5', template_id: 'linux-desktop-v1', driver: 'hyperv', mode: 'create_stopped_hardware',
    vm_name: `Bees-f586fe68-60a4-4cbb-a504-5961b709d764-${environment.id}`, cpu_count: 2, memory_bytes: 4096 * 1024 ** 2, disk_bytes: 30 * 1024 ** 3,
    image_iso_sha256: 'b'.repeat(64), image_iso_size: 792723456, payload_sha256: 'c'.repeat(64), payload_size: 387782226, catalog_hash: 'd'.repeat(64), network: 'none', storage_profile: 'bees-managed-v1', boot: false } }
