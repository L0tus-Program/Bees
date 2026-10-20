// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import type { ReactNode } from 'react'
import { createInstance } from 'i18next'
import type { i18n as I18n } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { setCsrfToken } from '../../api/client'
import type { Routine, RoutineOccurrence } from '../../api/routines'
import { routineResources } from '../../locales/routines'
import { productResources } from '../../locales/product'
import { agent as chatAgent } from '../chat/testFixtures'
import { RoutinesPanel } from './RoutinesPanel'
import { RoutineDescription } from './RoutineDescription'
import { RoutineEditor } from './RoutineEditor'
import { restoreRoutineIntent } from './recovery'
import { activeRoutine, agentId, conversationId, definition, occurrence, persistedRoutine as routine, revision, skill } from './fixtures'

let root: Root
let container: HTMLDivElement
let i18n: I18n
const agent = { ...chatAgent, id: agentId, conversation_id: conversationId }
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true); vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf'); sessionStorage.clear()
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'pt-BR', resources: Object.fromEntries(Object.entries(routineResources).map(([language, routines]) => [language, { routines, product: productResources[language as keyof typeof productResources] }])), interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); sessionStorage.clear(); vi.unstubAllGlobals(); vi.resetAllMocks(); setCsrfToken() })
async function render(node: ReactNode = <RoutinesPanel agent={agent} />) { await act(async () => root.render(<I18nextProvider i18n={i18n}>{node}</I18nextProvider>)) }
function button(text: string) { const value = [...container.querySelectorAll('button')].find((item) => item.textContent === text); if (!value) throw new Error('Missing button ' + text); return value }
async function click(value: HTMLElement) { await act(async () => value.click()) }
async function submit() { await act(async () => container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))) }
async function field(label: string, value: string) {
  const parent = [...container.querySelectorAll('label')].find((item) => item.textContent?.startsWith(label))
  const input = parent?.querySelector('input,textarea,select') as HTMLInputElement | undefined
  if (!input) throw new Error('Missing field ' + label)
  const setter = Object.getOwnPropertyDescriptor(input instanceof HTMLSelectElement ? HTMLSelectElement.prototype : input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value')!.set!
  await act(async () => { setter.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })) })
}
function queries(current: Routine[] = [routine], history: RoutineOccurrence[] = [occurrence]) {
  return vi.fn(async (url: string, options: RequestInit) => {
    if (options.method !== 'GET') throw new Error('Unexpected write')
    if (url.includes('/skills')) return Response.json([skill])
    if (url.includes('/occurrences')) return Response.json(history)
    if (url.includes('/revisions')) return Response.json([revision])
    if (url.includes(`/routines/${routine.id}`)) return Response.json(current[0])
    return Response.json(current)
  })
}

describe('human routine definitions', () => {
  it('loads canonical next/history using reads only and explicitly identifies scheduler unavailability', async () => {
    const fetcher = queries([activeRoutine]); vi.stubGlobal('fetch', fetcher)
    await render()
    expect(container.textContent).toContain('O agendador automático ainda está em preparação')
    expect(container.textContent).toContain('Elegível para agendador futuro')
    expect(container.querySelector('time')?.dateTime).toBe(activeRoutine.next_run_at)
    expect(container.querySelector('time')?.textContent).toContain('09:00')
    expect(container.querySelector('time')?.textContent).toContain('America/Sao_Paulo')
    await click(button('Ocorrências e revisões'))
    expect(document.activeElement).toBe(container.querySelector('.routine-history h3'))
    expect(container.textContent).toContain('Horário perdido por indisponibilidade ou atraso')
    expect(container.textContent).toContain('Revisões humanas')
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks') && !url.includes('/chat'))).toBe(true)
  })
  it('reviews complete inputs, destination and timezone before saving a paused definition', async () => {
    let current: Routine[] = []
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'POST') {
        const body = JSON.parse(options.body as string)
        current = [{ ...routine, name: body.definition.name, instructions: body.definition.instructions, schedule: body.definition.schedule, timezone: body.definition.timezone, definition: body.definition }]
        return Response.json(current[0])
      }
      return Response.json(current)
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Nova agenda'))
    await field('Nome da agenda', 'Resumo de ciência'); await field('Tema escolhido', 'Ciência'); await field('Fuso IANA', 'America/Sao_Paulo')
    await submit()
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'POST')).toHaveLength(0)
    expect(container.textContent).toContain('Ciência'); expect(container.textContent).toContain(conversationId)
    expect(container.textContent).toContain('Salvar cria uma revisão pausada')
    expect(document.activeElement).toBe(container.querySelector('.routine-review h3'))
    await click(button('Confirmar este pedido'))
    const write = fetcher.mock.calls.find(([, options]) => options.method === 'POST')!
    expect(JSON.parse(write[1].body as string)).toMatchObject({ definition: { name: 'Resumo de ciência', inputs: { tema: 'Ciência' }, timezone: 'America/Sao_Paulo' } })
    expect(JSON.parse(write[1].body as string)).not.toHaveProperty('active')
    expect(container.textContent).toContain('Pedido confirmado')
    expect(document.activeElement?.textContent).toContain('Pedido confirmado')
    expect(container.textContent).toContain('Pausada')
  })
  it('preserves edited form values when returning from the concrete review', async () => {
    vi.stubGlobal('fetch', queries())
    await render(); await click(button('Revisar agenda'))
    await field('Nome da agenda', 'Revisada na interface'); await submit(); await click(button('Voltar'))
    expect((container.querySelector('.routine-editor input') as HTMLInputElement).value).toBe('Revisada na interface')
  })
  it.each(['Pausar agenda', 'Cancelar rotina'])('requires confirmation before %s with exact CAS', async (action) => {
    let current = activeRoutine
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') { const body = JSON.parse(options.body as string); current = { ...activeRoutine, revision: 2, status: body.status, next_run_at: null }; return Response.json(current) }
      return Response.json([current])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button(action))
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(0)
    expect(container.textContent).toContain(action === 'Pausar agenda' ? 'Pausar cancela ocorrências pendentes' : 'Cancelar é definitivo')
    await click(button('Confirmar este pedido'))
    const write = fetcher.mock.calls.find(([, options]) => options.method === 'PUT')!
    expect(JSON.parse(write[1].body as string)).toMatchObject({ expected_revision: 1, status: action === 'Pausar agenda' ? 'paused' : 'cancelled' })
    expect(write[0]).toContain(`/routines/${routine.id}/status`)
  })
  it('prepares future eligibility without pretending to execute the schedule', async () => {
    let current = routine
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') { current = { ...activeRoutine, revision: 2 }; return Response.json(current) }
      return Response.json([current])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Preparar agenda futura'))
    expect(container.textContent).toContain('O agendador automático ainda não está disponível')
    await click(button('Confirmar este pedido'))
    expect(container.textContent).toContain('Elegível para agendador futuro')
    expect(fetcher.mock.calls.filter(([, options]) => options.method !== 'GET')).toHaveLength(1)
    expect(fetcher.mock.calls.every(([url]) => !url.includes('/tasks'))).toBe(true)
  })
  it('queries before retrying an uncertain status write with the same UUID and body', async () => {
    let attempts = 0; let current = activeRoutine
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') { attempts++; current = { ...routine, revision: 2 }; if (attempts === 1) throw new TypeError('Lost response'); return Response.json(current) }
      return Response.json(url.includes(`/routines/${routine.id}`) ? current : [current])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Pausar agenda')); await click(button('Confirmar este pedido'))
    expect(button('Nova agenda').disabled).toBe(true)
    expect(restoreRoutineIntent(agentId)?.body.client_request_id).toBeTruthy()
    const before = fetcher.mock.calls.length
    await click(button('Consultar e verificar o mesmo pedido'))
    const recovery = fetcher.mock.calls.slice(before)
    expect(recovery[0][1].method).toBe('GET'); expect(recovery[0][0]).toContain(`/routines/${routine.id}`)
    expect(recovery[1][1].method).toBe('PUT')
    const writes = fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')
    expect(writes[0][1].body).toBe(writes[1][1].body)
    expect(restoreRoutineIntent(agentId)).toBeNull()
  })
  it('restores uncertain creation after remount and checks state before repeating the original request', async () => {
    let attempts = 0; let current: Routine[] = []
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'POST') { attempts++; const body = JSON.parse(options.body as string); current = [{ ...routine, name: body.definition.name, instructions: body.definition.instructions, schedule: body.definition.schedule, timezone: body.definition.timezone, definition: body.definition }]; if (attempts === 1) throw new TypeError('Lost response'); return Response.json(current[0]) }
      return Response.json(current)
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Nova agenda')); await field('Tema escolhido', 'Ciência'); await submit(); await click(button('Confirmar este pedido'))
    const original = restoreRoutineIntent(agentId)
    await render(<RoutinesPanel key="reloaded" agent={agent} />)
    expect(button('Nova agenda').disabled).toBe(true)
    expect(restoreRoutineIntent(agentId)).toEqual(original)
    const before = fetcher.mock.calls.length
    await click(button('Consultar e verificar o mesmo pedido'))
    expect(fetcher.mock.calls[before][1].method).toBe('GET')
    const writes = fetcher.mock.calls.filter(([, options]) => options.method === 'POST')
    expect(writes).toHaveLength(2); expect(writes[0][1].body).toBe(writes[1][1].body)
    expect(restoreRoutineIntent(agentId)).toBeNull()
  })
  it('does not retry a write when the required state query fails', async () => {
    let attempts = 0
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') { attempts++; throw new TypeError('Lost response') }
      if (url.includes(`/routines/${routine.id}`)) throw new TypeError('Offline')
      return Response.json([activeRoutine])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Pausar agenda')); await click(button('Confirmar este pedido')); await click(button('Consultar e verificar o mesmo pedido'))
    expect(attempts).toBe(1); expect(button('Nova agenda').disabled).toBe(true); expect(restoreRoutineIntent(agentId)).not.toBeNull()
  })
  it('refreshes conflicts instead of replacing CAS or silently resubmitting', async () => {
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') return Response.json({ error: { code: 'revision_conflict' } }, { status: 409 })
      return Response.json([activeRoutine])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Pausar agenda')); await click(button('Confirmar este pedido'))
    expect(container.textContent).toContain('A rotina mudou em outra sessão')
    expect(container.querySelector('.routine-review')).toBeNull()
    expect(fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')).toHaveLength(1)
    expect(restoreRoutineIntent(agentId)).toBeNull()
  })
  it('shows legacy metadata without future eligibility and never evaluates it on read', async () => {
    vi.stubGlobal('fetch', queries([{ ...routine, origin: 'legacy', definition: null, schedule: { cron: '*' } }]))
    await render()
    expect(container.textContent).toContain('Metadado legado: não executa')
    expect([...container.querySelectorAll('button')].some((item) => item.textContent === 'Preparar agenda futura')).toBe(false)
  })
  it('renders text safely and provides matching translations for all three languages', async () => {
    for (const language of ['pt-BR', 'en', 'es']) {
      await act(async () => { await i18n.changeLanguage(language) })
      await render(<RoutineDescription definition={{ ...definition, instructions: '<script>steal()</script>' }} />)
      expect(container.querySelector('script')).toBeNull(); expect(container.textContent).toContain('<script>steal()</script>')
      expect(container.textContent).not.toContain('notification_always'); expect(container.textContent).toContain('America/Sao_Paulo')
    }
    expect(Object.keys(routineResources.en).sort()).toEqual(Object.keys(routineResources['pt-BR']).sort())
    expect(Object.keys(routineResources.es).sort()).toEqual(Object.keys(routineResources['pt-BR']).sort())
  })
  it('offers labelled keyboard forms and native weekday controls in each language', async () => {
    for (const language of ['pt-BR', 'en', 'es']) {
      await act(async () => { await i18n.changeLanguage(language) })
      const onReview = vi.fn()
      await render(<RoutineEditor key={language} initial={{ definition: { ...definition, schedule: { frequency: 'weekly', time: '09:00', weekdays: [0, 4] } }, source: skill.current.definition }} skills={[skill]} conversationId={conversationId} disabled={false} onReview={onReview} onCancel={() => {}} />)
      expect(container.querySelectorAll('input[type=checkbox]')).toHaveLength(7)
      expect(container.querySelectorAll('input[type=checkbox]:checked')).toHaveLength(2)
      for (const input of container.querySelectorAll('input,textarea,select')) expect(input.closest('label')).not.toBeNull()
      await submit(); expect(onReview).toHaveBeenCalledOnce()
    }
  })
})

describe('routine history boundaries', () => {
  it('paginates occurrences separately and displays the historical revision timezone', async () => {
    const first = Array.from({ length: 50 }, (_, index) => ({ ...occurrence,
      id: `55555555-5555-4555-8555-${String(index).padStart(12, '0')}`,
      scheduled_at: new Date(Date.UTC(2026, 9, 16 - index, 12)).toISOString(),
      window_end: new Date(Date.UTC(2026, 9, 16 - index, 12)).toISOString(),
    }))
    const last = { ...occurrence, id: '66666666-6666-4666-8666-666666666666', scheduled_at: '2026-08-01T12:00:00Z', window_end: '2026-08-01T12:00:00Z' }
    const current = { ...routine, timezone: 'UTC', definition: { ...definition, timezone: 'UTC' } }
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method !== 'GET') throw new Error('Unexpected write')
      if (url.includes('/skills')) return Response.json([skill])
      if (url.includes('/occurrences')) return Response.json(url.includes('offset=50') ? [last] : first)
      if (url.includes('/revisions')) return Response.json([revision])
      return Response.json([current])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Ocorrências e revisões'))
    expect(container.querySelector('.routine-history time')?.textContent).toContain('09:00')
    expect(container.querySelector('.routine-history time')?.textContent).toContain('America/Sao_Paulo')
    await click(button('Carregar mais ocorrências'))
    expect(container.querySelectorAll('.routine-history ol > li')).toHaveLength(51)
    expect(fetcher.mock.calls.some(([url]) => url.includes('/occurrences?limit=50&offset=50'))).toBe(true)
    expect(fetcher.mock.calls.filter(([url]) => url.includes('/revisions'))).toHaveLength(1)
  })
  it('preserves failed skill queries independently of a successful routine query', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/skills')) throw new TypeError('Offline')
      return Response.json([routine])
    }))
    await render()
    expect(container.querySelector('[role=alert]')).not.toBeNull()
    expect(button('Nova agenda').disabled).toBe(true)
    expect(container.textContent).toContain(routine.name)
  })
})

describe('routine uncertainty safety', () => {
  it('keeps an uncertain request in memory when optional session storage is blocked', async () => {
    let attempts = 0; let current = activeRoutine
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') { attempts++; current = { ...routine, revision: 2 }; if (attempts === 1) throw new TypeError('Lost response'); return Response.json(current) }
      return Response.json(url.includes(`/routines/${routine.id}`) ? current : [current])
    }); vi.stubGlobal('fetch', fetcher)
    const originalStorage = sessionStorage
    vi.stubGlobal('sessionStorage', { getItem: (key: string) => originalStorage.getItem(key), setItem: () => { throw new Error('Blocked') }, removeItem: (key: string) => originalStorage.removeItem(key), clear: () => originalStorage.clear() })
    try {
      await render(); await click(button('Pausar agenda')); await click(button('Confirmar este pedido'))
      expect(container.textContent).toContain('Mantenha esta aba aberta')
      await click(button('Consultar e verificar o mesmo pedido'))
      const writes = fetcher.mock.calls.filter(([, options]) => options.method === 'PUT')
      expect(writes).toHaveLength(2); expect(writes[0][1].body).toBe(writes[1][1].body)
    } finally { vi.stubGlobal('sessionStorage', originalStorage) }
  })
  it('treats an invalid successful response as uncertain rather than allowing a new intent', async () => {
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes('/skills')) return Response.json([skill])
      if (options.method === 'PUT') return Response.json({ status: 'completed' })
      return Response.json([activeRoutine])
    }); vi.stubGlobal('fetch', fetcher)
    await render(); await click(button('Pausar agenda')); await click(button('Confirmar este pedido'))
    expect(button('Nova agenda').disabled).toBe(true)
    expect(container.textContent).toContain('A resposta do pedido ficou incerta')
    expect(restoreRoutineIntent(agentId)).not.toBeNull()
  })
})
