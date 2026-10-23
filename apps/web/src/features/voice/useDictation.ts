import { useEffect, useRef, useState } from 'react'
import { recognitionConstructor, speechLanguage } from './speech'
import type { Recognition } from './speech'

/** Ditado incremental: texto final é entregue por onText; parcial fica em interim. */
export function useDictation(language: string, onText: (text: string) => void) {
  const [listening, setListening] = useState(false)
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recognition = useRef<Recognition | null>(null)
  const callback = useRef(onText)
  useEffect(() => { callback.current = onText }, [onText])
  useEffect(() => () => recognition.current?.abort(), [])
  const Constructor = recognitionConstructor()

  function start() {
    if (!Constructor || listening) return
    const instance = new Constructor()
    instance.lang = speechLanguage(language)
    instance.continuous = true
    instance.interimResults = true
    instance.onresult = (event) => {
      let partial = ''
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        if (result.isFinal) callback.current(result[0].transcript.trim())
        else partial += result[0].transcript
      }
      setInterim(partial)
    }
    instance.onerror = (event) => { setError(event.error) }
    instance.onend = () => { setListening(false); setInterim(''); recognition.current = null }
    recognition.current = instance
    setError(null); setListening(true)
    try { instance.start() } catch { setListening(false); setError('start_failed') }
  }
  function stop() { recognition.current?.stop() }
  return { supported: !!Constructor, listening, interim, error, start, stop }
}
