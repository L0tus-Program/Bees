// @vitest-environment happy-dom
import { act } from 'react'
import { createRoot } from 'react-dom/client'
import type { Root } from 'react-dom/client'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ReadAloud } from './ReadAloud'
import { speakableText, speechLanguage } from './speech'
import { useDictation } from './useDictation'
import { voiceResources } from '../../locales/voice'

let root: Root
let container: HTMLDivElement
let i18n: ReturnType<typeof createInstance>
const spoken: { text: string; lang: string }[] = []
class Utterance { text: string; lang = ''; onend: (() => void) | null = null; onerror: (() => void) | null = null; constructor(text: string) { this.text = text } }
const instances: FakeRecognition[] = []
class FakeRecognition {
  lang = ''; continuous = false; interimResults = false
  onresult: ((event: unknown) => void) | null = null; onend: (() => void) | null = null; onerror: ((event: { error: string }) => void) | null = null
  constructor() { instances.push(this) }
  start() {}
  stop() { this.onend?.() }
  abort() {}
}
beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  spoken.length = 0
  vi.stubGlobal('SpeechSynthesisUtterance', Utterance)
  vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: (item: Utterance) => spoken.push({ text: item.text, lang: item.lang }) })
  vi.stubGlobal('webkitSpeechRecognition', FakeRecognition)
  container = document.createElement('div'); document.body.append(container); root = createRoot(container)
  i18n = createInstance(); await i18n.init({ lng: 'en', resources: { en: { voice: voiceResources.en } }, interpolation: { escapeValue: false } })
})
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals() })

it('cleans markdown and maps languages', () => {
  expect(speakableText('**Olá** [fonte](https://x.org) `código` https://y.org\n# Título')).toBe('Olá fonte código Título')
  expect(speechLanguage('es')).toBe('es-ES')
  expect(speechLanguage('pt-BR')).toBe('pt-BR')
})

it('reads a reply aloud in the interface language', async () => {
  await act(async () => root.render(<I18nextProvider i18n={i18n}><ReadAloud text="Hello **there**" /></I18nextProvider>))
  const button = container.querySelector('button')!
  expect(button.textContent).toBe('Listen')
  await act(async () => button.click())
  expect(spoken).toEqual([{ text: 'Hello there', lang: 'en-US' }])
  expect(button.textContent).toBe('Stop reading')
})

it('dictation delivers final text and exposes interim results', async () => {
  const received: string[] = []
  let api: ReturnType<typeof useDictation> | null = null
  function Probe() { api = useDictation('es', (text) => received.push(text)); return <span>{api.interim}</span> }
  await act(async () => root.render(<Probe />))
  expect(api!.supported).toBe(true)
  await act(async () => api!.start())
  expect(instances.at(-1)!.lang).toBe('es-ES')
  await act(async () => instances.at(-1)!.onresult!({ resultIndex: 0, results: [{ isFinal: false, 0: { transcript: 'hola' } }] }))
  expect(container.textContent).toBe('hola')
  await act(async () => instances.at(-1)!.onresult!({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: ' hola mundo ' } }] }))
  expect(received).toEqual(['hola mundo'])
  await act(async () => api!.stop())
  expect(api!.listening).toBe(false)
})

it.each(['en', 'es'] as const)('has the same keys in %s', (language) => {
  expect(Object.keys(voiceResources[language]).sort()).toEqual(Object.keys(voiceResources['pt-BR']).sort())
})
