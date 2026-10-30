// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { useResource } from './useResource'

afterEach(() => vi.unstubAllGlobals())

it('keeps the current data on screen while reloading', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  let resolve: (value: string) => void = () => {}
  let calls = 0
  const load = () => { calls += 1; return calls === 1 ? Promise.resolve('primeira') : new Promise<string>((done) => { resolve = done }) }
  const states: string[] = []
  let reload: () => void = () => {}
  function Probe() {
    const value = useResource(load)
    reload = value.reload
    states.push(value.resource.state === 'ready' ? value.resource.data : value.resource.state)
    return null
  }
  const container = document.createElement('div'); const root = createRoot(container)
  await act(async () => root.render(<Probe />))
  expect(states.at(-1)).toBe('primeira')
  await act(async () => reload())
  expect(states.at(-1)).toBe('primeira')
  await act(async () => resolve('segunda'))
  expect(states.at(-1)).toBe('segunda')
  expect(states.slice(states.indexOf('primeira'))).not.toContain('loading')
  await act(async () => root.unmount())
})
