// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import type { i18n } from 'i18next'
import type { Computer } from '../../api/computers'
import { computerResources } from '../../locales/computer'
import { ScreenViewer } from './ScreenViewer'

const clients = vi.hoisted(() => [] as { emit(name: string): void; disconnect: ReturnType<typeof vi.fn>; viewOnly: boolean; scaleViewport: boolean }[])
vi.mock('@novnc/novnc', () => ({ default: class extends EventTarget {
  viewOnly = true
  scaleViewport = false
  disconnect = vi.fn(() => this.emit('disconnect'))
  constructor() { super(); clients.push(this) }
  emit(name: string) { this.dispatchEvent(new Event(name)) }
} }))
const computer: Computer = { id: 'c1', agent_id: 'a1', executor_id: 'e1', name: 'PC', status: 'ready', revision: 1, control_holder: 'agent', control_revision: 1, reason_code: null, online: true, created_at: '', updated_at: '' }
let root: Root, container: HTMLDivElement, instance: i18n
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  clients.length = 0
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  instance = createInstance()
  await instance.init({ lng: 'pt-BR', resources: { 'pt-BR': { computer: computerResources['pt-BR'] } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })
async function show(value = computer) {
  await act(async () => root.render(<I18nextProvider i18n={instance}><ScreenViewer computer={value} /></I18nextProvider>))
}
const status = () => container.querySelector('[role="status"]')?.textContent
const emit = async (index: number, name: string) => act(async () => clients[index].emit(name))

it.each([{ id: 'c2' }, { agent_id: 'a2' }, { control_holder: 'human' as const }])('starts a replacement connection in connecting state: %j', async (patch) => {
  await show(); await emit(0, 'connect')
  expect(status()).toContain('Tela conectada')
  await show({ ...computer, ...patch })
  expect(clients).toHaveLength(2)
  expect(clients[0].disconnect).toHaveBeenCalledTimes(1)
  expect(status()).toContain('Conectando à tela')
})
it.each(['connect', 'disconnect'])('ignores an old %s event after a replacement connects', async (event) => {
  await show(); await show({ ...computer, control_holder: 'human' })
  await emit(1, 'connect'); await emit(0, event)
  expect(status()).toContain('Tela conectada')
  expect(container.querySelector('button')).toBeNull()
  expect(clients[1].viewOnly).toBe(false)
})
it('does not let an old connect event hide the current disconnected state', async () => {
  await show(); await show({ ...computer, id: 'c2' })
  await emit(1, 'disconnect'); await emit(0, 'connect')
  expect(status()).toContain('Tela desconectada')
  expect(container.querySelector('button')?.textContent).toBe('Reconectar')
})
it('reconnects explicitly and ignores events from the abandoned client', async () => {
  await show(); await emit(0, 'disconnect')
  await act(async () => container.querySelector('button')!.click())
  expect(clients).toHaveLength(2)
  expect(status()).toContain('Conectando à tela')
  await emit(0, 'connect')
  expect(status()).toContain('Conectando à tela')
  await emit(1, 'connect')
  expect(status()).toContain('Tela conectada')
})
it('keeps the connection and status across polling snapshots of the same computer', async () => {
  await show(); await emit(0, 'connect')
  await show({ ...computer, revision: 2, control_revision: 2, online: false, name: 'Updated' })
  expect(clients).toHaveLength(1)
  expect(clients[0].disconnect).not.toHaveBeenCalled()
  expect(status()).toContain('Tela conectada')
})
it('does not create a client when unmounted before the import settles', async () => {
  act(() => root.render(<I18nextProvider i18n={instance}><ScreenViewer computer={computer} /></I18nextProvider>))
  act(() => root.render(null))
  await act(async () => {})
  expect(clients).toHaveLength(0)
})
