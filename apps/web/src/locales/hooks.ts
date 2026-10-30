const pt = {
  title: 'Gatilho por webhook', help: 'Dispare esta rotina a partir de outro serviço (Zapier, IFTTT, GitHub, formulários) com um POST na URL secreta. O conteúdo enviado é ignorado; a rotina roda com as entradas e permissões definidas aqui.',
  none: 'Sem gatilho.', create: 'Criar gatilho', rotate: 'Gerar nova URL', revoke: 'Revogar gatilho', interval: 'Intervalo mínimo entre disparos (segundos)',
  urlOnce: 'Copie agora: esta URL contém um segredo e não será mostrada de novo.', copy: 'Copiar', copied: 'Copiada.',
  active: 'Ativo · {{count}} disparos', last: 'Último: {{time}} ({{result}})', revokeConfirm: 'Revogar faz a URL parar de funcionar imediatamente. Continuar?',
  rotateConfirm: 'A URL atual deixará de funcionar. Continuar?',
}
type Translations = typeof pt
const en: Translations = {
  title: 'Webhook trigger', help: 'Trigger this routine from another service (Zapier, IFTTT, GitHub, forms) with a POST to the secret URL. The sent content is ignored; the routine runs with the inputs and permissions defined here.',
  none: 'No trigger.', create: 'Create trigger', rotate: 'Generate new URL', revoke: 'Revoke trigger', interval: 'Minimum interval between triggers (seconds)',
  urlOnce: 'Copy it now: this URL contains a secret and will not be shown again.', copy: 'Copy', copied: 'Copied.',
  active: 'Active · {{count}} triggers', last: 'Last: {{time}} ({{result}})', revokeConfirm: 'Revoking makes the URL stop working immediately. Continue?',
  rotateConfirm: 'The current URL will stop working. Continue?',
}
const es: Translations = {
  title: 'Disparador por webhook', help: 'Dispara esta rutina desde otro servicio (Zapier, IFTTT, GitHub, formularios) con un POST a la URL secreta. El contenido enviado se ignora; la rutina corre con las entradas y permisos definidos aquí.',
  none: 'Sin disparador.', create: 'Crear disparador', rotate: 'Generar nueva URL', revoke: 'Revocar disparador', interval: 'Intervalo mínimo entre disparos (segundos)',
  urlOnce: 'Cópiala ahora: esta URL contiene un secreto y no se volverá a mostrar.', copy: 'Copiar', copied: 'Copiada.',
  active: 'Activo · {{count}} disparos', last: 'Último: {{time}} ({{result}})', revokeConfirm: 'Revocar hace que la URL deje de funcionar de inmediato. ¿Continuar?',
  rotateConfirm: 'La URL actual dejará de funcionar. ¿Continuar?',
}
export const hookResources = { 'pt-BR': pt, en, es }
