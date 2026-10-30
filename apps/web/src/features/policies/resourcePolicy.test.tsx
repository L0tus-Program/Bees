import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { PolicyRecord } from '../../api/policies'
import { policyResources } from '../../locales/policies'
import { resourceApprovalResources } from '../../locales/resourceApprovals'
import { agent } from '../chat/testFixtures'
import { resourceApproval as approval } from '../approvals/resourceFixtures'
import { editablePolicy, editableResourcePolicy } from './state'
import { PolicyForm } from './PolicyForm'

const policy: PolicyRecord = { id: 'rule', agent_id: approval.agent_id, name: 'Regra exata', effect: 'allow', scope: approval.scope, status: 'active', revision: 1, reason: '', created_at: approval.created_at, updated_at: approval.created_at, origin: 'approval' }
describe('exact resource policy management', () => {
  it('only edits the recognized complete scope without authority expansion', () => {
    expect(editableResourcePolicy(policy, approval.agent_id)).toBe(true)
    expect(editablePolicy(policy, approval.agent_id)).toBe(true)
    for (const scope of [{ ...policy.scope, action: 'shell' }, { ...policy.scope, resource: '*' }, { ...policy.scope, parameters: {} }, { ...policy.scope, identity: 'admin' }]) expect(editableResourcePolicy({ ...policy, scope }, approval.agent_id)).toBe(false)
    expect(editableResourcePolicy(policy, 'other')).toBe(false)
  })
  it.each(['pt-BR', 'en', 'es'] as const)('shows an immutable exact scope while editing its effect in %s', async (language) => {
    const i18n = createInstance(); await i18n.init({ lng: language, resources: { [language]: { policies: policyResources[language], resourceApprovals: resourceApprovalResources[language] } } })
    const html = renderToStaticMarkup(<I18nextProvider i18n={i18n}><PolicyForm agent={{ ...agent, id: approval.agent_id }} policy={policy} onSaved={() => {}} onCancel={() => {}} onReload={() => {}} /></I18nextProvider>)
    expect(html).toContain(approval.target.resource_id); expect(html).toContain(approval.input_hash)
    expect(html).toContain('id="policy-scope" disabled=""'); expect(html).not.toContain('value="all"')
    expect(html).toContain(policyResources[language].approvalEditImpact)
  })
})
