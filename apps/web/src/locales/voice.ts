const pt = {
  listen: 'Ouvir', stop: 'Parar leitura',
  dictate: 'Ditar', stopDictation: 'Parar ditado', listening: 'Ouvindo…',
  dictationHelp: 'O ditado usa o reconhecimento de voz do navegador; alguns navegadores enviam o áudio ao serviço do fabricante.',
  voiceReplies: 'Ler respostas em voz alta', voiceRepliesHelp: 'Novas respostas da abelha são lidas automaticamente neste navegador.',
  error_not_allowed: 'Permita o microfone para ditar.', 'error_no-speech': 'Nenhuma fala detectada.', 'error_not-allowed': 'Permita o microfone para ditar.',
  error_generic: 'O ditado foi interrompido.',
}
type Translations = typeof pt
const en: Translations = {
  listen: 'Listen', stop: 'Stop reading',
  dictate: 'Dictate', stopDictation: 'Stop dictation', listening: 'Listening…',
  dictationHelp: 'Dictation uses the browser’s speech recognition; some browsers send audio to the vendor’s service.',
  voiceReplies: 'Read replies aloud', voiceRepliesHelp: 'New replies from the bee are read automatically in this browser.',
  error_not_allowed: 'Allow the microphone to dictate.', 'error_no-speech': 'No speech detected.', 'error_not-allowed': 'Allow the microphone to dictate.',
  error_generic: 'Dictation was interrupted.',
}
const es: Translations = {
  listen: 'Escuchar', stop: 'Detener lectura',
  dictate: 'Dictar', stopDictation: 'Detener dictado', listening: 'Escuchando…',
  dictationHelp: 'El dictado usa el reconocimiento de voz del navegador; algunos navegadores envían el audio al servicio del fabricante.',
  voiceReplies: 'Leer respuestas en voz alta', voiceRepliesHelp: 'Las nuevas respuestas de la abeja se leen automáticamente en este navegador.',
  error_not_allowed: 'Permite el micrófono para dictar.', 'error_no-speech': 'No se detectó voz.', 'error_not-allowed': 'Permite el micrófono para dictar.',
  error_generic: 'El dictado se interrumpió.',
}
export const voiceResources = { 'pt-BR': pt, en, es }
