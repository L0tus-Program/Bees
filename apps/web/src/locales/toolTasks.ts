const pt = {
  choice: 'Permitir transformação local de texto nesta tarefa',
  help: 'O modelo pode propor remover espaços ou converter letras do texto fornecido. Até 4 gerações, 16 operações e 120 segundos ativos. Pode consumir créditos. As decisões de ferramenta continuam separadas; isto não concede acesso a arquivos, rede, terminal ou apps.',
  files: 'Para pedir arquivos, use a tarefa de texto sem esta opção.',
  pending: 'A criação pode ter sido registrada. O pedido desta aba foi preservado; não criaremos outra tarefa automaticamente.',
  verify: 'Verificar o mesmo pedido', corrupt: 'Não foi possível ler o pedido preservado. Consulte as tarefas antes de iniciar outro pedido.',
  discard: 'Descartar pedido desta aba', discardHelp: 'Isso não cancela uma tarefa que já exista. Confira as tarefas registradas antes de pedir outra.',
  storage: 'Não foi possível preservar o pedido nesta aba. Nenhuma nova criação foi enviada.',
  private: 'O pedido fica nesta aba até a confirmação. Não inclua credenciais na descrição da tarefa.',
  partial: 'Esta execução parou com progresso parcial. Confira o motivo e as operações registradas antes de retomar.',
  scope: 'Delegue uma tarefa ao modelo e continue conversando. A transformação local de texto é opcional quando disponível; computadores e apps têm suas próprias integrações.',
}
type Translations = Record<keyof typeof pt, string>
const en: Translations = {
  choice: 'Allow local text transformation in this task', help: 'The model can propose trimming spaces or changing letter case in supplied text. Up to 4 generations, 16 operations and 120 active seconds. May consume credits. Tool decisions stay separate; this does not grant access to files, networks, terminals or apps.',
  files: 'To request files, use the text task without this option.', pending: 'Creation may have been recorded. This tab’s request was preserved; we will not automatically create another task.', verify: 'Check the same request', corrupt: 'The preserved request cannot be read. Check recorded tasks before starting another request.',
  discard: 'Discard this tab’s request', discardHelp: 'This does not cancel an existing task. Check recorded tasks before requesting another.', storage: 'The request could not be preserved in this tab. No new creation was sent.', private: 'The request stays in this tab until confirmed. Do not include credentials in the task description.',
  partial: 'This execution stopped with partial progress. Check the reason and recorded operations before resuming.', scope: 'Delegate a task to the model and keep chatting. Local text transformation is optional when available; computers and apps have their own integrations.',
}
const es: Translations = {
  choice: 'Permitir transformación local de texto en esta tarea', help: 'El modelo puede proponer quitar espacios o cambiar mayúsculas del texto proporcionado. Hasta 4 generaciones, 16 operaciones y 120 segundos activos. Puede consumir créditos. Las decisiones de herramienta siguen separadas; esto no concede acceso a archivos, red, terminal ni apps.',
  files: 'Para pedir archivos, usa la tarea de texto sin esta opción.', pending: 'La creación puede haberse registrado. Se conservó la solicitud de esta pestaña; no crearemos otra tarea automáticamente.', verify: 'Comprobar la misma solicitud', corrupt: 'No se puede leer la solicitud conservada. Consulta las tareas antes de iniciar otra.',
  discard: 'Descartar solicitud de esta pestaña', discardHelp: 'Esto no cancela una tarea existente. Consulta las tareas registradas antes de pedir otra.', storage: 'No se pudo conservar la solicitud en esta pestaña. No se envió una nueva creación.', private: 'La solicitud queda en esta pestaña hasta la confirmación. No incluyas credenciales en la descripción.',
  partial: 'Esta ejecución se detuvo con progreso parcial. Revisa el motivo y las operaciones registradas antes de retomar.', scope: 'Delega una tarea al modelo y sigue conversando. La transformación local de texto es opcional cuando está disponible; ordenadores y apps tienen sus propias integraciones.',
}
export const toolTaskResources = { 'pt-BR': pt, en, es }
