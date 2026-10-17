const pt = {
  navigation: 'Skills', title: 'Procedimentos reutilizáveis', description: 'Guarde passos, entradas e critérios de conclusão para cada abelha. Consulte o histórico e revise cada nova versão.',
  executionScope: 'Esta biblioteca salva procedimentos. A execução integrada em tarefas ainda está em preparação.',
  create: 'Novo procedimento', edit: 'Criar nova versão', name: 'Nome', purpose: 'Objetivo', inputs: 'Entradas', steps: 'Passos', output: 'Entrega esperada', completion: 'Como conferir a conclusão',
  required: 'Obrigatória', optional: 'Opcional', inputName: 'Identificador da entrada', inputDescription: 'O que informar', inputNumber: 'Entrada {{number}}', stepNumber: 'Passo {{number}}', stepTitle: 'Título do passo', instruction: 'Instrução', addInput: 'Adicionar entrada', addStep: 'Adicionar passo', remove: 'Remover',
  environment: 'Ambiente necessário', environment_none: 'Apenas texto', environment_own: 'Computador da abelha', environment_personal: 'Máquina pessoal autorizada', tools: 'Ferramentas necessárias', none: 'Nenhuma', toolsHelp: 'Identificadores separados por vírgula. Declarar uma ferramenta não concede acesso a ela.',
  save: 'Salvar versão desabilitada', saving: 'Salvando…', saveHelp: 'Cada edição conserva o histórico e começa desabilitada. Não inclua senhas, chaves ou outros segredos.', saved: 'Versão salva. Consulte o estado atual na lista.', cancel: 'Cancelar',
  enable: 'Revisar e habilitar', disable: 'Desabilitar', enabled: 'Habilitada', disabled: 'Desabilitada', reviewEnable: 'Revisar versão {{version}}', confirmEnable: 'Habilitar esta versão', enableHelp: 'Habilite somente depois de revisar os passos e os recursos declarados. As permissões e políticas continuam sendo avaliadas quando houver execução.',
  version: 'Versão {{version}}', history: 'Revisões', origin_human: 'Edição humana', origin_import: 'Cópia importada', export: 'Exportar versão', import: 'Importar procedimento', reviewImport: 'Revisar cópia importada',
  refresh: 'Atualizar lista', loading: 'Carregando procedimentos…', more: 'Carregar mais', empty: 'Nenhum procedimento salvo nesta abelha.', conflict: 'O procedimento mudou em outra sessão. A lista foi atualizada; revise antes de tentar novamente.',
  lost: 'Não foi possível confirmar a gravação. Verifique o mesmo pedido antes de fazer outra alteração.', verify: 'Verificar o mesmo pedido',
}
const en: Record<keyof typeof pt, string> = {
  navigation: 'Skills', title: 'Reusable procedures', description: 'Save steps, inputs and completion criteria for each bee. Browse the history and review each new version.',
  executionScope: 'This library stores procedures. Integrated task execution is still in preparation.',
  create: 'New procedure', edit: 'Create new version', name: 'Name', purpose: 'Purpose', inputs: 'Inputs', steps: 'Steps', output: 'Expected deliverable', completion: 'How to verify completion',
  required: 'Required', optional: 'Optional', inputName: 'Input identifier', inputDescription: 'What to provide', inputNumber: 'Input {{number}}', stepNumber: 'Step {{number}}', stepTitle: 'Step title', instruction: 'Instruction', addInput: 'Add input', addStep: 'Add step', remove: 'Remove',
  environment: 'Required environment', environment_none: 'Text only', environment_own: 'Bee computer', environment_personal: 'Authorized personal machine', tools: 'Required tools', none: 'None', toolsHelp: 'Comma-separated identifiers. Declaring a tool does not grant access to it.',
  save: 'Save disabled version', saving: 'Saving…', saveHelp: 'Each edit preserves the history and starts disabled. Do not include passwords, keys or other secrets.', saved: 'Version saved. Check the current state in the list.', cancel: 'Cancel',
  enable: 'Review and enable', disable: 'Disable', enabled: 'Enabled', disabled: 'Disabled', reviewEnable: 'Review version {{version}}', confirmEnable: 'Enable this version', enableHelp: 'Enable only after reviewing the steps and declared resources. Permissions and policies must still be checked when execution is available.',
  version: 'Version {{version}}', history: 'Revisions', origin_human: 'Human edit', origin_import: 'Imported copy', export: 'Export version', import: 'Import procedure', reviewImport: 'Review imported copy',
  refresh: 'Refresh list', loading: 'Loading procedures…', more: 'Load more', empty: 'No procedures saved for this bee.', conflict: 'The procedure changed in another session. The list was refreshed; review before trying again.',
  lost: 'The write could not be confirmed. Check the same request before making another change.', verify: 'Check the same request',
}
const es: Record<keyof typeof pt, string> = {
  navigation: 'Skills', title: 'Procedimientos reutilizables', description: 'Guarda pasos, entradas y criterios de conclusión para cada abeja. Consulta el historial y revisa cada nueva versión.',
  executionScope: 'Esta biblioteca guarda procedimientos. La ejecución integrada en tareas sigue en preparación.',
  create: 'Nuevo procedimiento', edit: 'Crear nueva versión', name: 'Nombre', purpose: 'Objetivo', inputs: 'Entradas', steps: 'Pasos', output: 'Entrega esperada', completion: 'Cómo comprobar la conclusión',
  required: 'Obligatoria', optional: 'Opcional', inputName: 'Identificador de entrada', inputDescription: 'Qué informar', inputNumber: 'Entrada {{number}}', stepNumber: 'Paso {{number}}', stepTitle: 'Título del paso', instruction: 'Instrucción', addInput: 'Añadir entrada', addStep: 'Añadir paso', remove: 'Quitar',
  environment: 'Entorno necesario', environment_none: 'Solo texto', environment_own: 'Ordenador de la abeja', environment_personal: 'Máquina personal autorizada', tools: 'Herramientas necesarias', none: 'Ninguna', toolsHelp: 'Identificadores separados por comas. Declarar una herramienta no concede acceso a ella.',
  save: 'Guardar versión deshabilitada', saving: 'Guardando…', saveHelp: 'Cada edición conserva el historial y empieza deshabilitada. No incluyas contraseñas, claves ni otros secretos.', saved: 'Versión guardada. Consulta el estado actual en la lista.', cancel: 'Cancelar',
  enable: 'Revisar y habilitar', disable: 'Deshabilitar', enabled: 'Habilitada', disabled: 'Deshabilitada', reviewEnable: 'Revisar versión {{version}}', confirmEnable: 'Habilitar esta versión', enableHelp: 'Habilita solo después de revisar los pasos y los recursos declarados. Los permisos y las políticas deben comprobarse cuando haya ejecución.',
  version: 'Versión {{version}}', history: 'Revisiones', origin_human: 'Edición humana', origin_import: 'Copia importada', export: 'Exportar versión', import: 'Importar procedimiento', reviewImport: 'Revisar copia importada',
  refresh: 'Actualizar lista', loading: 'Cargando procedimientos…', more: 'Cargar más', empty: 'No hay procedimientos guardados para esta abeja.', conflict: 'El procedimiento cambió en otra sesión. La lista se actualizó; revisa antes de intentarlo de nuevo.',
  lost: 'No se pudo confirmar la escritura. Comprueba la misma solicitud antes de realizar otro cambio.', verify: 'Comprobar la misma solicitud',
}
export const skillResources = { 'pt-BR': pt, en, es }
