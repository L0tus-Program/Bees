const pt = {
  scope: 'Avisos por conversa', allConversations: 'Todas as conversas da abelha', untitledConversation: 'Conversa sem título', scopeHelp: 'Cada tarefa aparece em sua conversa de execução. Escolha pelo título e data. O filtro vale para esta visualização; ao reabrir o painel, todas as conversas são exibidas.', loadingConversations: 'Carregando conversas…', retryConversations: 'Consultar conversas novamente', moreConversations: 'Mais conversas', preferenceScope: 'Estas preferências valem para todas as conversas desta abelha.', scopedDescription: 'Avisos somente da conversa selecionada. Leitura e silêncio preservam o histórico.',
  navigation: 'Atenção', title: 'Atenção da abelha', description: 'Resultados, falhas e decisões das tarefas textuais ficam disponíveis aqui.',
  unread: '{{count}} avisos não lidos', loading: 'Carregando atenção…', refresh: 'Atualizar', more: 'Ver mais', empty: 'Nenhum aviso registrado.',
  stale: 'A lista anterior foi preservada. Atualize antes de editar.', openTask: 'Abrir tarefa', markRead: 'Marcar como lido', markUnread: 'Marcar como não lido',
  read: 'Lido', unreadLabel: 'Não lido', muted: 'Aviso silenciado', pending: 'Precisa de atenção', resolved: 'Decisão encerrada ou indisponível',
  preferences: 'Preferências de aviso', preferencesHelp: 'Silenciar um aviso mantém o registro no histórico. Falhas e decisões continuam acessíveis; marcar como lido não resolve uma tarefa nem concede permissão.',
  enabled: 'Avisar', disabled: 'Silenciar', saving: 'Salvando…', saved: 'Alteração confirmada.', conflict: 'O estado mudou. Consulte a atualização antes de editar.',
  lost: 'Não foi possível confirmar a alteração. Consulte o estado ou repita o mesmo pedido.', retry: 'Repetir o pedido', cancel: 'Consultar estado',
  kinds: { task_completed: 'Tarefa concluída', task_failed: 'Tarefa falhou', approval_required: 'Decisão necessária', outcome_unknown: 'Resultado desconhecido' },
  unknownHelp: 'O efeito pode ter ocorrido. A leitura deste aviso não libera uma nova tentativa.',
}
const en: typeof pt = {
  scope: 'Notices by conversation', allConversations: 'All bee conversations', untitledConversation: 'Untitled conversation', scopeHelp: 'Each task appears in its execution conversation. Choose by title and date. This filter applies to this view; reopening the panel shows all conversations.', loadingConversations: 'Loading conversations…', retryConversations: 'Read conversations again', moreConversations: 'More conversations', preferenceScope: 'These preferences apply to all conversations of this bee.', scopedDescription: 'Notices only from the selected conversation. Reading and muting preserve history.',
  navigation: 'Attention', title: 'Bee attention', description: 'Results, failures and decisions from text tasks remain available here.',
  unread: '{{count}} unread notices', loading: 'Loading attention…', refresh: 'Refresh', more: 'Show more', empty: 'No notices recorded.',
  stale: 'The previous list was preserved. Refresh before editing.', openTask: 'Open task', markRead: 'Mark as read', markUnread: 'Mark as unread',
  read: 'Read', unreadLabel: 'Unread', muted: 'Notice muted', pending: 'Needs attention', resolved: 'Decision closed or unavailable',
  preferences: 'Notice preferences', preferencesHelp: 'Muting a notice keeps its history. Failures and decisions remain accessible; marking as read does not resolve a task or grant permission.',
  enabled: 'Notify', disabled: 'Mute', saving: 'Saving…', saved: 'Change confirmed.', conflict: 'The state changed. Check the refreshed data before editing.',
  lost: 'The change could not be confirmed. Read the current state or repeat the same request.', retry: 'Repeat request', cancel: 'Read current state',
  kinds: { task_completed: 'Task completed', task_failed: 'Task failed', approval_required: 'Decision needed', outcome_unknown: 'Unknown outcome' },
  unknownHelp: 'The effect may have occurred. Reading this notice does not allow another attempt.',
}
const es: typeof pt = {
  scope: 'Avisos por conversación', allConversations: 'Todas las conversaciones de la abeja', untitledConversation: 'Conversación sin título', scopeHelp: 'Cada tarea aparece en su conversación de ejecución. Elige por título y fecha. El filtro se aplica a esta vista; al reabrir el panel se muestran todas las conversaciones.', loadingConversations: 'Cargando conversaciones…', retryConversations: 'Consultar conversaciones de nuevo', moreConversations: 'Más conversaciones', preferenceScope: 'Estas preferencias se aplican a todas las conversaciones de esta abeja.', scopedDescription: 'Avisos solo de la conversación seleccionada. Leer y silenciar conserva el historial.',
  navigation: 'Atención', title: 'Atención de la abeja', description: 'Resultados, fallos y decisiones de tareas textuales permanecen disponibles aquí.',
  unread: '{{count}} avisos sin leer', loading: 'Cargando atención…', refresh: 'Actualizar', more: 'Ver más', empty: 'No hay avisos registrados.',
  stale: 'Se conservó la lista anterior. Actualiza antes de editar.', openTask: 'Abrir tarea', markRead: 'Marcar como leído', markUnread: 'Marcar como no leído',
  read: 'Leído', unreadLabel: 'Sin leer', muted: 'Aviso silenciado', pending: 'Necesita atención', resolved: 'Decisión cerrada o no disponible',
  preferences: 'Preferencias de aviso', preferencesHelp: 'Silenciar un aviso conserva el historial. Fallos y decisiones siguen accesibles; marcar como leído no resuelve una tarea ni concede permiso.',
  enabled: 'Avisar', disabled: 'Silenciar', saving: 'Guardando…', saved: 'Cambio confirmado.', conflict: 'El estado cambió. Consulta los datos actualizados antes de editar.',
  lost: 'No se pudo confirmar el cambio. Consulta el estado o repite la misma solicitud.', retry: 'Repetir solicitud', cancel: 'Consultar estado',
  kinds: { task_completed: 'Tarea completada', task_failed: 'La tarea falló', approval_required: 'Decisión necesaria', outcome_unknown: 'Resultado desconocido' },
  unknownHelp: 'El efecto puede haber ocurrido. Leer este aviso no permite otro intento.',
}
export const attentionResources = { 'pt-BR': pt, en, es }
