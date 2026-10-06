import type { ApprovalCollection, ApprovalRecord } from '../../api/approvals'

export function approvalUsable(approval: ApprovalRecord, now = Date.now()) {
  return approval.valid && approval.consumed_at === null && Date.parse(approval.expires_at) > now
}
export function approvalDecidable(approval: ApprovalRecord, now = Date.now()) {
  return approval.status === 'pending' && approvalUsable(approval, now)
}
export function approvalRevocable(approval: ApprovalRecord) {
  return approval.status === 'approved' && approval.decision === 'allow_once' && approval.consumed_at === null
}
export function mergeApprovalPage(previous: ApprovalCollection | null, incoming: ApprovalCollection): ApprovalCollection {
  const records = new Map(previous?.approvals.map((approval) => [approval.id, approval]))
  incoming.approvals.forEach((approval) => {
    const prior = records.get(approval.id)
    if (!prior || prior.revision <= approval.revision) records.set(approval.id, approval)
  })
  return { ...incoming, approvals: [...records.values()] }
}
// Only an explicit human submit opens the editor. Received model text is never inspected.
export function isAutonomyCommand(text: string) { return text.trim() === '/autonomia' }
