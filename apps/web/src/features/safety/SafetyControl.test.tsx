import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ReactElement } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { ApiError } from '../../api/client'
import type { SafetyState } from '../../api/safety'
import { safetyResources } from '../../locales/safety'
import { SafetyConfirmation, SafetyControl, SafetyStatusView } from './SafetyControl'
import { checkedSafetySnapshot, inFlightTotal, safetyFailure } from './state'
import type { SafetyIntent } from './state'

const flight = { model_calls: 0, tool_actions: 0, chat_reservations: 0, provisioning_effects: 0 }
const running: SafetyState = { snapshot: { event_seq: 1, observed_at: '2026-10-12T12:00:00Z' }, status: 'running', generation: 0, revision: 1, changed_at: '1970-01-01T00:00:00+00:00', reason: null, in_flight: flight }
const stopped: SafetyState = { ...running, status: 'stopped', generation: 1, revision: 2, changed_at: '2026-10-10T12:00:00+00:00', reason: 'Revisão de custos', in_flight: { ...flight, model_calls: 2, tool_actions: 1 } }
const intent: SafetyIntent = { kind: 'stop', revision: 1, requestId: 'request-1', reason: '', sent: false }
const noop = () => undefined

async function render(element: ReactElement, language = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, fallbackLng: 'pt-BR', resources: { 'pt-BR': { safety: safetyResources['pt-BR'] }, en: { safety: safetyResources.en }, es: { safety: safetyResources.es } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{element}</I18nextProvider>)
}

describe('global stop control', () => {
  it('uses a neutral loading label before any canonical state is available', async () => {
    const html = await render(<SafetyControl />)
    expect(html).toContain('aria-label="Consultando estado da execução…"')
    expect(html).not.toContain('Execução ativa')
  })
  it('refuses stale authority but accepts fewer in-flight operations at the same human revision', () => {
    expect(checkedSafetySnapshot({ ...stopped, in_flight: flight, snapshot: { ...stopped.snapshot, observed_at: '2026-10-12T12:01:00Z' } }, stopped).in_flight).toEqual(flight)
    for (const change of [{ revision: 1 }, { generation: 0 }, { status: 'running' as const }, { reason: 'Rewritten' }, { changed_at: '2026-10-10T11:00:00Z' }]) expect(() => checkedSafetySnapshot({ ...stopped, ...change }, stopped)).toThrow('invalid_response')
    expect(() => checkedSafetySnapshot({ ...stopped, snapshot: { ...stopped.snapshot, event_seq: 0 } }, stopped)).toThrow('invalid_response')
  })
  it('offers stopping while running without inventing in-flight work', async () => {
    const html = await render(<SafetyStatusView state={running} disabled={false} onStart={noop} />)
    expect(html).toContain('Execução ativa')
    expect(html).toContain('Parar tudo')
    expect(html).not.toContain('Operações já enviadas')
  })
  it('shows the stop, its reason and what is still finishing', async () => {
    const html = await render(<SafetyStatusView state={stopped} disabled={false} onStart={noop} />)
    for (const text of ['Bees parado', 'Retomar', 'Motivo: Revisão de custos', 'Chamadas de tarefas ao modelo: <strong>2</strong>', 'Ações de ferramentas: <strong>1</strong>']) expect(html).toContain(text)
    expect(html).not.toContain('Etapas de provisionamento')
    expect(inFlightTotal(stopped)).toBe(3)
    const idle = await render(<SafetyStatusView state={{ ...stopped, reason: null, in_flight: flight }} disabled={false} onStart={noop} />)
    expect(idle).toContain('Nenhuma operação em andamento.')
    expect(idle).not.toContain('Motivo:')
  })
  it('explains effects before confirming and keeps the reason fixed after sending', async () => {
    const draft = await render(<SafetyConfirmation intent={intent} busy={false} lost={false} onReason={noop} onConfirm={noop} onCancel={noop} />)
    expect(draft).toContain('não é cancelado no provedor')
    expect(draft).toContain('Confirmar parada')
    expect(draft).not.toMatch(/<input[^>]*disabled/)
    const lost = await render(<SafetyConfirmation intent={{ ...intent, reason: 'x', sent: true }} busy={false} lost onReason={noop} onConfirm={noop} onCancel={noop} />)
    expect(lost).toContain('o mesmo pedido será reconhecido')
    expect(lost).toContain('Tentar novamente')
    expect(lost).toMatch(/<input[^>]*disabled/)
    const resume = await render(<SafetyConfirmation intent={{ ...intent, kind: 'resume' }} busy={false} lost={false} onReason={noop} onConfirm={noop} onCancel={noop} />, 'en')
    expect(resume).toContain('unknown result keep waiting')
    expect(resume).not.toContain('<input')
  })
  it('classifies failures: lost replays, conflicts reload, others stop', () => {
    expect(safetyFailure(new ApiError('mutation_network'))).toBe('lost')
    expect(safetyFailure(new ApiError('mutation_timeout'))).toBe('lost')
    expect(safetyFailure(new ApiError('http_503', 503))).toBe('lost')
    expect(safetyFailure(new ApiError('revision_conflict', 409))).toBe('conflict')
    expect(safetyFailure(new ApiError('invalid_response', 200))).toBe('conflict')
    expect(safetyFailure(new ApiError('validation_error', 422))).toBe('error')
  })
  it('localizes spanish labels', async () => {
    expect(await render(<SafetyStatusView state={stopped} disabled={false} onStart={noop} />, 'es')).toContain('Reanudar')
  })
})
