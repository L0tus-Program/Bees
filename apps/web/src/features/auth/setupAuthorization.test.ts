import { afterEach, describe, expect, it, vi } from 'vitest'
import { createSetupAuthorization, validSetupToken } from './setupAuthorization'
import { authenticate } from '../../api/onboarding'

const testToken = 'A'.repeat(43)
const locationFor = (hash: string) => ({ hash, pathname: '/', search: '?language=pt-BR' })

afterEach(() => { vi.unstubAllGlobals() })

describe('launcher setup authorization', () => {
  it('limpa o fragmento antes de validar e guarda a autorização apenas em memória', () => {
    const authorization = createSetupAuthorization()
    const replaceState = vi.fn(() => expect(authorization.available()).toBe(false))
    authorization.capture(locationFor(`#setup=${testToken}`), { replaceState })
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/?language=pt-BR')
    expect(JSON.stringify(replaceState.mock.calls)).not.toContain(testToken)
    expect(authorization.available()).toBe(true)
    expect(authorization.forSetup(false)).toBe(testToken)
  })

  it.each([
    '#setup=', '#setup', '#setup=short', `#setup=${'A'.repeat(44)}`,
    `#setup=${'A'.repeat(42)}!`, '#setup=%ZZ', '#setup=%E0%A4%A',
    `#setup=${testToken}&setup=${testToken}`, `#setup=${testToken}&redirect=https://example.test`,
    `#setup=${testToken}%0A`, `#unexpected=${testToken}`, '#setup?malformed',
  ])('recusa fragmento inválido ou ambíguo e remove a URL sensível (caso %#)', (hash) => {
    const authorization = createSetupAuthorization()
    const replaceState = vi.fn()
    authorization.capture(locationFor(hash), { replaceState })
    expect(authorization.available()).toBe(false)
    expect(authorization.forSetup(false)).toBeUndefined()
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/?language=pt-BR')
  })

  it('aceita token percent-encoded válido sem manter caracteres do fragmento na URL', () => {
    const authorization = createSetupAuthorization()
    authorization.capture(locationFor(`#setup=%41${'A'.repeat(42)}`), { replaceState: vi.fn() })
    expect(authorization.forSetup(false)).toBe(testToken)
  })

  it('não cria autorização ou altera histórico em uma abertura sem fragmento', () => {
    const authorization = createSetupAuthorization()
    const replaceState = vi.fn()
    authorization.capture(locationFor(''), { replaceState })
    expect(authorization.available()).toBe(false)
    expect(replaceState).not.toHaveBeenCalled()
  })

  it('reload da URL já limpa perde a autorização e exige reabrir pelo launcher', () => {
    const first = createSetupAuthorization()
    const browserLocation = locationFor(`#setup=${testToken}`)
    first.capture(browserLocation, { replaceState: () => { browserLocation.hash = '' } })
    expect(first.available()).toBe(true)
    const reloaded = createSetupAuthorization()
    reloaded.capture(browserLocation, { replaceState: vi.fn() })
    expect(reloaded.forSetup(false)).toBeUndefined()
  })

  it('uma instalação configurada descarta o token e não o utiliza no login', async () => {
    const authorization = createSetupAuthorization()
    authorization.capture(locationFor(`#setup=${testToken}`), { replaceState: vi.fn() })
    expect(authorization.forSetup(true, testToken)).toBeUndefined()
    expect(authorization.available()).toBe(false)
    const fetcher = vi.fn().mockResolvedValue(Response.json({ configured: true, authenticated: true }))
    vi.stubGlobal('fetch', fetcher)
    await authenticate({ password: 'test-password-only' })
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/auth/login')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ password: 'test-password-only' })
    expect(fetcher.mock.calls[0][1].body).not.toContain(testToken)
  })

  it('sucesso limpa a autorização; fallback avançado aceita somente formato válido', () => {
    const authorization = createSetupAuthorization()
    authorization.capture(locationFor(`#setup=${testToken}`), { replaceState: vi.fn() })
    authorization.clear()
    expect(authorization.available()).toBe(false)
    expect(authorization.forSetup(false)).toBeUndefined()
    expect(authorization.forSetup(false, 'invalid')).toBeUndefined()
    expect(authorization.forSetup(false, ` ${testToken} `)).toBe(testToken)
    expect(validSetupToken(testToken)).toBe(true)
    expect(validSetupToken(' '.repeat(43))).toBe(false)
  })

  it('o módulo de entrada remove o fragmento automaticamente antes da aplicação', async () => {
    const replaceState = vi.fn()
    vi.stubGlobal('window', { location: locationFor(`#setup=${testToken}`), history: { replaceState } })
    vi.resetModules()
    const entry = await import('./setupAuthorization')
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(null, '', '/?language=pt-BR')
    expect(entry.setupAuthorization.available()).toBe(true)
    entry.setupAuthorization.clear()
  })
})
