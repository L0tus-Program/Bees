const pt = {
  heading: 'Arquivos da tarefa', scope: 'Cada cartão representa uma versão preservada. O download baixa essa versão; o conteúdo não é aberto aqui.',
  loading: 'Carregando arquivos…', empty: 'Nenhum arquivo registrado nesta tarefa.', refresh: 'Atualizar arquivos', more: 'Carregar mais arquivos',
  readError: 'Não foi possível carregar os arquivos. Atualize para tentar novamente.', stale: 'Os arquivos anteriores continuam visíveis; a lista pode estar desatualizada.',
  download: 'Baixar versão {{version}}', downloading: 'Preparando download…', sent: 'Download enviado ao navegador.',
  downloadError: 'O download não foi concluído. Confira a sessão e tente novamente.', integrityError: 'O arquivo não corresponde à versão registrada. Nenhum download foi enviado.',
  notReadyError: 'Esta versão ainda não está disponível para download. Atualize os arquivos.', sizeError: 'A interface baixa arquivos de até 25 MiB.',
  format: 'Formato', size: 'Tamanho', version: 'Versão', hash: 'SHA-256', noHash: 'Ainda não registrado',
  provenance: 'Vínculos e integridade', task: 'Tarefa', run: 'Execução', noRun: 'Sem execução vinculada', artifact: 'Arquivo', series: 'Série', previous: 'Versão anterior',
  created: 'Registrado em', status_draft: 'Em preparação', status_ready: 'Disponível', status_failed: 'Falha na publicação', status_deleted: 'Removido',
  unavailable: 'Download disponível somente após a publicação confirmada.', bytes: '{{value}} bytes',
}
const en: Record<keyof typeof pt, string> = {
  heading: 'Task files', scope: 'Each card represents a preserved version. Download retrieves that version; its content is not opened here.',
  loading: 'Loading files…', empty: 'No files registered in this task.', refresh: 'Refresh files', more: 'Load more files',
  readError: 'Could not load files. Refresh to try again.', stale: 'Previously loaded files remain visible; the list may be outdated.',
  download: 'Download version {{version}}', downloading: 'Preparing download…', sent: 'Download sent to the browser.',
  downloadError: 'The download did not complete. Check your session and try again.', integrityError: 'The file does not match the registered version. No download was sent.',
  notReadyError: 'This version is not available for download yet. Refresh the files.', sizeError: 'The interface downloads files up to 25 MiB.',
  format: 'Format', size: 'Size', version: 'Version', hash: 'SHA-256', noHash: 'Not registered yet',
  provenance: 'Links and integrity', task: 'Task', run: 'Run', noRun: 'No linked run', artifact: 'File', series: 'Series', previous: 'Previous version',
  created: 'Registered on', status_draft: 'Preparing', status_ready: 'Available', status_failed: 'Publication failed', status_deleted: 'Removed',
  unavailable: 'Download is available only after publication is confirmed.', bytes: '{{value}} bytes',
}
const es: Record<keyof typeof pt, string> = {
  heading: 'Archivos de la tarea', scope: 'Cada tarjeta representa una versión preservada. La descarga obtiene esa versión; su contenido no se abre aquí.',
  loading: 'Cargando archivos…', empty: 'Ningún archivo registrado en esta tarea.', refresh: 'Actualizar archivos', more: 'Cargar más archivos',
  readError: 'No se pudieron cargar los archivos. Actualiza para intentarlo de nuevo.', stale: 'Los archivos anteriores siguen visibles; la lista puede estar desactualizada.',
  download: 'Descargar versión {{version}}', downloading: 'Preparando descarga…', sent: 'Descarga enviada al navegador.',
  downloadError: 'La descarga no se completó. Comprueba la sesión e inténtalo de nuevo.', integrityError: 'El archivo no coincide con la versión registrada. No se envió ninguna descarga.',
  notReadyError: 'Esta versión aún no está disponible para descargar. Actualiza los archivos.', sizeError: 'La interfaz descarga archivos de hasta 25 MiB.',
  format: 'Formato', size: 'Tamaño', version: 'Versión', hash: 'SHA-256', noHash: 'Aún no registrado',
  provenance: 'Vínculos e integridad', task: 'Tarea', run: 'Ejecución', noRun: 'Sin ejecución vinculada', artifact: 'Archivo', series: 'Serie', previous: 'Versión anterior',
  created: 'Registrado el', status_draft: 'En preparación', status_ready: 'Disponible', status_failed: 'Publicación fallida', status_deleted: 'Eliminado',
  unavailable: 'La descarga está disponible solo tras confirmar la publicación.', bytes: '{{value}} bytes',
}
export const artifactResources = { 'pt-BR': pt, en, es }
