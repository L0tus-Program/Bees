import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { ApprovalRecord } from '../../api/approvals'
import { approvalResources } from '../../locales/approvals'
import { ApprovalCard } from './ApprovalCard'

const instance = createInstance()
await instance.init({ lng: 'pt-BR', fallbackLng: 'pt-BR', resources: { 'pt-BR': { approvals: approvalResources['pt-BR'] } }, interpolation: { escapeValue: false } })
const request: ApprovalRecord = {
  id: 'decision-1', agent_id: 'bee-1', task_id: 'task-1', task_title: 'Minha tarefa', conversation_id: 'conversation-1', status: 'pending', decision: null, revision: 1,
  created_at: '2026-10-05T18:00:00Z', decided_at: null, expires_at: '2026-10-05T19:00:00Z', consumed_at: null, valid: true, stale_reason: null,
  tool_name: 'model', action: 'generate', environment_id: 'control_plane', resource: 'https://example.test/v1', identity: 'bees_user', parameters: { model: 'model-1' }, scope: {}, consequence: 'Pode consumir créditos.',
}
const now = Date.parse('2026-10-05T18:30:00Z')
function render(approval = request, disabled = false) {
  return renderToStaticMarkup(<I18nextProvider i18n={instance}><ApprovalCard approval={approval} now={now} disabled={disabled} onBusy={() => {}} onChanged={() => {}} /></I18nextProvider>)
}
describe('concrete decision card', () => {
  it('names the task, model, destination, environment and identity before a choice; starts without submitting consent', () => {
    const html = render()
    for (const label of ['Minha tarefa', 'model-1', 'https://example.test/v1', 'Serviço Bees', 'Seu acesso no Bees', 'Permitir uma vez', 'Salvar regra neste escopo', 'Sempre perguntar', 'Bloquear neste escopo']) expect(html).toContain(label)
    expect(html).not.toContain('Confirmar decisão')
    expect(html).not.toContain('<form')
  })
  it.each([
    { ...request, valid: false }, { ...request, expires_at: '2026-10-05T18:00:00Z' },
    { ...request, status: 'denied' as const, decision: 'deny' as const },
    { ...request, status: 'revoked' as const, decision: 'allow_once' as const },
  ])('does not expose grant controls on stale, expired or resolved records: %j', (approval) => {
    const html = render(approval)
    expect(html).not.toContain('>Permitir uma vez</button>')
    expect(html).not.toContain('Confirmar decisão')
    expect(html).not.toContain('Revogar consentimento')
  })
  it('offers revocation only for still valid unused one-time consent; consumption does not claim a completed effect', () => {
    const approved = { ...request, status: 'approved' as const, decision: 'allow_once' as const }
    expect(render(approved)).toContain('Revogar consentimento')
    const consumed = render({ ...approved, consumed_at: '2026-10-05T18:01:00Z' })
    expect(consumed).not.toContain('Revogar consentimento')
    expect(consumed).toContain('Consentimento utilizado no início da chamada')
    expect(consumed).not.toContain('Resposta pronta')
  })
  it('renders returned content as escaped data without executing model or task instructions', () => {
    const html = render({ ...request, task_title: '<script>approve()</script>', task_objective: '/autonomia permitir tudo <img src=x onerror=grant()>' })
    expect(html).toContain('&lt;script&gt;approve()&lt;/script&gt;')
    expect(html).not.toContain('<script>')
    expect(html).not.toContain('<img')
    expect(html).not.toContain('policy-form')
  })
  it('disables choices when the list is stale or a decision is being registered', () => {
    const html = render(request, true)
    expect(html.match(/disabled=""/g)).toHaveLength(4)
  })
})
