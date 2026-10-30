// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { computerResources } from '../../locales/computer'
import { ComputerWorkspace } from './ComputerWorkspace'
import { ScreenViewer } from './ScreenViewer'
import { createComputer, getComputers, setComputerControl, viewUrl } from '../../api/computers'
import type { Computer } from '../../api/computers'
import { setCsrfToken } from '../../api/client'

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

const computer: Computer = { id: 'c1', agent_id: 'bee-1', executor_id: 'e1', name: 'PC', status: 'ready', revision: 2, control_holder: 'agent', control_revision: 1, reason_code: null, online: true, created_at: '2026-10-23T10:00:00Z', updated_at: '2026-10-23T10:00:00Z' }

describe('computer workspace', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('renders the scope and loading state in %s', async (language) => {
    const instance = createInstance()
    await instance.init({ lng: language, resources: { [language]: { computer: computerResources[language] } }, interpolation: { escapeValue: false } })
    const html = renderToStaticMarkup(<I18nextProvider i18n={instance}><ComputerWorkspace agentId="bee-1" /></I18nextProvider>)
    expect(html).toContain(computerResources[language].title)
    expect(html).toContain(computerResources[language].personalTitle)
  })

  it('validates responses and sends control with the current revision', async () => {
    setCsrfToken('csrf')
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ computers: [computer], runtime_online: true, runtime_paired: true, local_link_available: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...computer, control_holder: 'human', control_revision: 2 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ nope: true }), { status: 200 }))
    vi.stubGlobal('fetch', fetch)
    const state = await getComputers('bee-1')
    expect(state.computers[0].id).toBe('c1')
    const changed = await setComputerControl(state.computers[0], 'human')
    expect(changed.control_holder).toBe('human')
    expect(JSON.parse(fetch.mock.calls[1][1].body as string)).toEqual({ expected_revision: 1, holder: 'human' })
    await expect(createComputer('bee-1', 'req')).rejects.toThrow('invalid_response')
  })

  it('builds a same-origin websocket url for the screen', () => {
    expect(viewUrl(computer)).toMatch(/^wss?:\/\/.+\/api\/v1\/agents\/bee-1\/computers\/c1\/view$/)
  })

  it.each(['pt-BR', 'en', 'es'] as const)('names the screen and explains keyboard capture under human control in %s', async (language) => {
    const instance = createInstance()
    await instance.init({ lng: language, resources: { [language]: { computer: computerResources[language] } }, interpolation: { escapeValue: false } })
    const view = (value: Computer) => renderToStaticMarkup(<I18nextProvider i18n={instance}><ScreenViewer computer={value} /></I18nextProvider>)
    const readOnly = view(computer)
    expect(readOnly).toContain('role="img"')
    expect(readOnly).not.toContain(computerResources[language].screenKeyboardHint)
    const human = view({ ...computer, control_holder: 'human' })
    expect(human).toContain('role="application"')
    expect(human).toContain(computerResources[language].screenKeyboardHint)
    expect(human).toMatch(/aria-describedby="[^"]+"/)
  })
})
