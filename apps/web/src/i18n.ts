import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { productResources } from './locales/product'
import { beeResources } from './locales/bee'
import { taskResources } from './locales/tasks'
import { policyResources } from './locales/policies'
import { approvalResources } from './locales/approvals'
import { toolResources } from './locales/tools'
import { environmentResources } from './locales/environments'
import { provisioningResources } from './locales/provisioning'

const ptBR = {
  language: 'Idioma',
  skip: 'Ir para o conteúdo',
  foundation: 'Fundação técnica',
  eyebrow: 'Sua colmeia, suas escolhas',
  title: 'Uma base para suas\nabelhas trabalharem.',
  introduction: 'Bees está começando: uma plataforma aberta para assistentes persistentes, com liberdade para escolher modelos e onde executar.',
  phase: 'Etapa atual',
  phaseTitle: 'Primeiro, a fundação.',
  phaseDescription: 'Esta versão verifica a comunicação entre a interface e o serviço. O ciclo de uso do assistente será construído nas próximas etapas.',
  healthTitle: 'Serviço Bees',
  healthDescription: 'Conexão real com a API desta instalação.',
  loading: 'Verificando conexão',
  online: 'Serviço disponível',
  offline: 'Conexão indisponível',
  onlineDescription: 'A API respondeu corretamente. A conexão confirma a fundação técnica; o assistente ainda não está disponível.',
  loadingDescription: 'Aguardando a resposta do serviço local. A verificação leva até 5 segundos.',
  network: 'Não conseguimos acessar a API. Confira se o serviço está iniciado em 127.0.0.1:8000 e tente novamente.',
  timeout: 'O serviço não respondeu em 5 segundos. Confira se ele está disponível e tente novamente.',
  http: 'A API respondeu com erro. Confira os logs do serviço antes de tentar novamente.',
  contract: 'A resposta não corresponde ao serviço Bees esperado. Confira o endereço e a versão da API.',
  retry: 'Tentar novamente',
  recheck: 'Verificar novamente',
  endpoint: 'Endpoint',
  version: 'Versão da API',
  checkedAt: 'Verificado às {{time}}',
  snapshot: 'Status da última verificação. Esta página não monitora a conexão continuamente.',
  scope: 'O que temos agora',
  webTitle: 'Interface web',
  webDescription: 'Uma base responsiva, com português, inglês e espanhol.',
  serviceTitle: 'API independente',
  serviceDescription: 'O serviço funciona separado da interface e expõe seu estado.',
  setupTitle: 'Ambiente reproduzível',
  setupDescription: 'Dependências fixadas e verificações para começar a evoluir o produto.',
  next: 'Próximas etapas',
  nextTitle: 'O assistente ainda está por vir.',
  nextDescription: 'Nenhum modelo, tarefa ou computador está conectado nesta versão.',
  assistantTitle: 'Abelha persistente',
  assistantDescription: 'Modelos, conversa, memória e tarefas em segundo plano.',
  ownTitle: 'Computador da abelha',
  ownDescription: 'VM com navegador e apps, acompanhamento e controle humano.',
  personalTitle: 'Sua máquina',
  personalDescription: 'Conector opcional, recursos autorizados e permissões editáveis.',
  unavailable: 'Ainda não disponível',
  footer: 'Bees · Independência começa na base.',
}

const en: Record<keyof typeof ptBR, string> = {
  language: 'Language', skip: 'Skip to content', foundation: 'Technical foundation',
  eyebrow: 'Your hive, your choices', title: 'A foundation for your\nbees to get to work.',
  introduction: 'Bees is getting started: an open platform for persistent assistants, with freedom to choose models and where to run them.',
  phase: 'Current stage', phaseTitle: 'First, the foundation.',
  phaseDescription: 'This release checks communication between the interface and the service. The assistant workflow will follow in future stages.',
  healthTitle: 'Bees service', healthDescription: 'A real connection to this installation’s API.',
  loading: 'Checking connection', online: 'Service available', offline: 'Connection unavailable',
  onlineDescription: 'The API responded correctly. This confirms the technical foundation; the assistant is not available yet.',
  loadingDescription: 'Waiting for the local service. This check takes up to 5 seconds.',
  network: 'We could not reach the API. Check that the service is running at 127.0.0.1:8000 and try again.',
  timeout: 'The service did not respond within 5 seconds. Check its availability and try again.',
  http: 'The API returned an error. Check the service logs before trying again.',
  contract: 'The response does not match the expected Bees service. Check the API address and version.',
  retry: 'Try again', recheck: 'Check again', endpoint: 'Endpoint', version: 'API version',
  checkedAt: 'Checked at {{time}}', snapshot: 'Status from the last check. This page does not continuously monitor the connection.',
  scope: 'What is available now', webTitle: 'Web interface',
  webDescription: 'A responsive foundation in Portuguese, English and Spanish.',
  serviceTitle: 'Independent API', serviceDescription: 'The service runs separately from the interface and reports its status.',
  setupTitle: 'Reproducible environment', setupDescription: 'Pinned dependencies and checks to start evolving the product.',
  next: 'Next stages', nextTitle: 'The assistant is still to come.',
  nextDescription: 'No model, task or computer is connected in this release.',
  assistantTitle: 'Persistent bee', assistantDescription: 'Models, conversation, memory and background tasks.',
  ownTitle: 'The bee’s computer', ownDescription: 'A VM with a browser and apps, monitoring and human control.',
  personalTitle: 'Your machine', personalDescription: 'An optional connector, authorized resources and editable permissions.',
  unavailable: 'Not available yet', footer: 'Bees · Independence starts with the foundation.',
}

const es: Record<keyof typeof ptBR, string> = {
  language: 'Idioma', skip: 'Ir al contenido', foundation: 'Base técnica',
  eyebrow: 'Tu colmena, tus decisiones', title: 'Una base para que tus\nabejas trabajen.',
  introduction: 'Bees está empezando: una plataforma abierta para asistentes persistentes, con libertad para elegir modelos y dónde ejecutarlos.',
  phase: 'Etapa actual', phaseTitle: 'Primero, la base.',
  phaseDescription: 'Esta versión comprueba la comunicación entre la interfaz y el servicio. El ciclo del asistente llegará en las próximas etapas.',
  healthTitle: 'Servicio Bees', healthDescription: 'Conexión real con la API de esta instalación.',
  loading: 'Comprobando conexión', online: 'Servicio disponible', offline: 'Conexión no disponible',
  onlineDescription: 'La API respondió correctamente. Esto confirma la base técnica; el asistente aún no está disponible.',
  loadingDescription: 'Esperando al servicio local. Esta comprobación tarda hasta 5 segundos.',
  network: 'No pudimos acceder a la API. Comprueba que el servicio esté iniciado en 127.0.0.1:8000 e inténtalo de nuevo.',
  timeout: 'El servicio no respondió en 5 segundos. Comprueba su disponibilidad e inténtalo de nuevo.',
  http: 'La API respondió con un error. Revisa los registros del servicio antes de intentarlo de nuevo.',
  contract: 'La respuesta no corresponde al servicio Bees esperado. Comprueba la dirección y la versión de la API.',
  retry: 'Reintentar', recheck: 'Comprobar de nuevo', endpoint: 'Endpoint', version: 'Versión de la API',
  checkedAt: 'Comprobado a las {{time}}', snapshot: 'Estado de la última comprobación. Esta página no supervisa la conexión continuamente.',
  scope: 'Lo que tenemos ahora', webTitle: 'Interfaz web',
  webDescription: 'Una base adaptable, en portugués, inglés y español.',
  serviceTitle: 'API independiente', serviceDescription: 'El servicio funciona separado de la interfaz e informa de su estado.',
  setupTitle: 'Entorno reproducible', setupDescription: 'Dependencias fijadas y comprobaciones para empezar a desarrollar el producto.',
  next: 'Próximas etapas', nextTitle: 'El asistente está por llegar.',
  nextDescription: 'Ningún modelo, tarea u ordenador está conectado en esta versión.',
  assistantTitle: 'Abeja persistente', assistantDescription: 'Modelos, conversación, memoria y tareas en segundo plano.',
  ownTitle: 'Ordenador de la abeja', ownDescription: 'VM con navegador y apps, seguimiento y control humano.',
  personalTitle: 'Tu máquina', personalDescription: 'Conector opcional, recursos autorizados y permisos editables.',
  unavailable: 'Aún no disponible', footer: 'Bees · La independencia comienza en la base.',
}

function getSavedLanguage() {
  try {
    return localStorage.getItem('bees.ui.language') ?? 'pt-BR'
  } catch {
    return 'pt-BR'
  }
}

void i18n.use(initReactI18next).init({
  resources: { 'pt-BR': { translation: ptBR, product: productResources['pt-BR'], bee: beeResources['pt-BR'], tasks: taskResources['pt-BR'], policies: policyResources['pt-BR'], approvals: approvalResources['pt-BR'], tools: toolResources['pt-BR'], environments: environmentResources['pt-BR'], provisioning: provisioningResources['pt-BR'] }, en: { translation: en, product: productResources.en, bee: beeResources.en, tasks: taskResources.en, policies: policyResources.en, approvals: approvalResources.en, tools: toolResources.en, environments: environmentResources.en, provisioning: provisioningResources.en }, es: { translation: es, product: productResources.es, bee: beeResources.es, tasks: taskResources.es, policies: policyResources.es, approvals: approvalResources.es, tools: toolResources.es, environments: environmentResources.es, provisioning: provisioningResources.es } },
  lng: getSavedLanguage(),
  fallbackLng: 'pt-BR',
  supportedLngs: ['pt-BR', 'en', 'es'],
  interpolation: { escapeValue: false },
})

i18n.on('languageChanged', (language) => {
  document.documentElement.lang = language
  try { localStorage.setItem('bees.ui.language', language) } catch { /* Storage is optional. */ }
})
document.documentElement.lang = i18n.resolvedLanguage ?? 'pt-BR'

export default i18n
