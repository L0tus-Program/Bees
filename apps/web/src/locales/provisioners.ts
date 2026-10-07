const pt = {
  heading: 'Provisionador local', open: 'Ver cadastros', close: 'Fechar cadastros',
  scope: 'Estes cadastros controlam a autoridade do provisionador. Cadastro ativo não confirma que o aplicativo esteja conectado ou que exista uma VM pronta.',
  empty: 'Nenhum provisionador cadastrado neste host. A inscrição guiada ainda está em preparação.',
  loading: 'Consultando cadastros…', refresh: 'Atualizar cadastros', more: 'Carregar mais cadastros',
  status_active: 'Cadastro ativo', status_revoked: 'Cadastro revogado', revoke: 'Revogar cadastro',
  confirmation: 'Revogar este provisionador?', impact: 'A revogação impede novos efeitos. Uma operação já iniciada pode continuar; resultados desconhecidos precisam de revisão. Isso não remove computadores ou dados.',
  confirm: 'Confirmar revogação', revoking: 'Revogando…', back: 'Voltar',
  readBeforeContinue: 'Não foi possível confirmar o estado atual. Atualize os cadastros antes de decidir novamente. A revogação não será repetida automaticamente.',
  changed: 'Este cadastro mudou. Feche a confirmação e revise o estado atual antes de decidir.',
}
const en: Record<keyof typeof pt, string> = {
  heading: 'Local provisioner', open: 'View registrations', close: 'Close registrations',
  scope: 'These registrations control provisioner authority. Active registration does not confirm that the application is connected or that a VM is ready.',
  empty: 'No provisioner is registered on this host. Guided enrollment is still being prepared.',
  loading: 'Loading registrations…', refresh: 'Refresh registrations', more: 'Load more registrations',
  status_active: 'Active registration', status_revoked: 'Revoked registration', revoke: 'Revoke registration',
  confirmation: 'Revoke this provisioner?', impact: 'Revocation prevents new effects. An operation already started may continue; unknown outcomes need review. This does not remove computers or data.',
  confirm: 'Confirm revocation', revoking: 'Revoking…', back: 'Back',
  readBeforeContinue: 'Could not confirm the current state. Refresh registrations before deciding again. Revocation will not be repeated automatically.',
  changed: 'This registration changed. Close the confirmation and review its current state before deciding.',
}
const es: Record<keyof typeof pt, string> = {
  heading: 'Provisionador local', open: 'Ver registros', close: 'Cerrar registros',
  scope: 'Estos registros controlan la autoridad del provisionador. Un registro activo no confirma que la aplicación esté conectada ni que haya una VM lista.',
  empty: 'No hay un provisionador registrado en este host. La inscripción guiada sigue en preparación.',
  loading: 'Consultando registros…', refresh: 'Actualizar registros', more: 'Cargar más registros',
  status_active: 'Registro activo', status_revoked: 'Registro revocado', revoke: 'Revocar registro',
  confirmation: '¿Revocar este provisionador?', impact: 'La revocación impide nuevos efectos. Una operación ya iniciada puede continuar; los resultados desconocidos requieren revisión. Esto no elimina ordenadores ni datos.',
  confirm: 'Confirmar revocación', revoking: 'Revocando…', back: 'Volver',
  readBeforeContinue: 'No se pudo confirmar el estado actual. Actualiza los registros antes de decidir de nuevo. La revocación no se repetirá automáticamente.',
  changed: 'Este registro cambió. Cierra la confirmación y revisa el estado actual antes de decidir.',
}
export const provisionerResources = { 'pt-BR': pt, en, es }
