import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { canSpeak, speak, stopSpeaking } from './speech'

export function ReadAloud({ text }: { text: string }) {
  const { t, i18n } = useTranslation('voice')
  const [speaking, setSpeaking] = useState(false)
  useEffect(() => () => { if (speaking) stopSpeaking() }, [speaking])
  if (!canSpeak() || !text.trim()) return null
  return <button type="button" className="text-button read-aloud" onClick={() => {
    if (speaking) { stopSpeaking(); setSpeaking(false); return }
    if (speak(text, i18n.resolvedLanguage ?? 'pt-BR', () => setSpeaking(false))) setSpeaking(true)
  }}>{t(speaking ? 'stop' : 'listen')}</button>
}
