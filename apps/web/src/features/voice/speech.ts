// Voz pelo navegador: ditado (SpeechRecognition) e leitura em voz alta (speechSynthesis).
// Nenhum áudio passa pelo Bees; navegadores podem usar serviço próprio de reconhecimento.

interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
export interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean
  onresult: ((event: RecognitionEvent) => void) | null
  onend: (() => void) | null
  onerror: ((event: { error: string }) => void) | null
  start(): void; stop(): void; abort(): void
}
type RecognitionConstructor = new () => Recognition

export function recognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const scope = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor }
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition ?? null
}

export function speechLanguage(language: string) {
  return language === 'en' ? 'en-US' : language === 'es' ? 'es-ES' : 'pt-BR'
}

export function canSpeak() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'
}

/** Remove marcações comuns de Markdown para leitura natural. */
export function speakableText(text: string) {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[*_#>|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 6000)
}

export function speak(text: string, language: string, onEnd?: () => void) {
  if (!canSpeak()) return false
  window.speechSynthesis.cancel()
  const utterance = new window.SpeechSynthesisUtterance(speakableText(text))
  utterance.lang = speechLanguage(language)
  if (onEnd) { utterance.onend = onEnd; utterance.onerror = onEnd }
  window.speechSynthesis.speak(utterance)
  return true
}

export function stopSpeaking() {
  if (canSpeak()) window.speechSynthesis.cancel()
}

const VOICE_KEY = 'bees:voice-replies'
export function readVoicePreference() {
  try { return window.localStorage.getItem(VOICE_KEY) === '1' } catch { return false }
}
export function writeVoicePreference(value: boolean) {
  try { window.localStorage.setItem(VOICE_KEY, value ? '1' : '0') } catch { /* preferência só nesta aba */ }
}
