const pt = {
  title: 'Começar de um modelo', choose: 'Modelo', none: 'Em branco', import: 'Importar modelo (.json)', export: 'Exportar como modelo',
  invalid: 'Arquivo de modelo inválido.', imported: 'Modelo “{{name}}” aplicado. Revise antes de criar.',
  help: 'Modelos preenchem nome, propósito e instruções. Nada de credenciais, memórias ou permissões é copiado.',
  toolsHint: 'Dica: este modelo funciona melhor com o modo agente (ferramentas recomendadas).',
  templates: {
    assistant: { label: 'Assistente pessoal', name: 'Assistente', purpose: 'Ajudar no dia a dia: responder dúvidas, organizar ideias, rascunhar mensagens e lembrar preferências.', instructions: 'Seja direto e cordial. Pergunte quando faltar contexto. Salve preferências importantes na memória quando eu pedir.', tools: false },
    researcher: { label: 'Pesquisador(a)', name: 'Pesquisadora', purpose: 'Pesquisar na web, comparar fontes e entregar relatórios com citações verificáveis.', instructions: 'Use o modo pesquisa. Prefira fontes primárias e recentes, cite cada afirmação e aponte divergências ou incertezas.', tools: true },
    news: { label: 'Monitor de notícias', name: 'Radar', purpose: 'Acompanhar notícias de temas escolhidos e enviar resumos curtos e periódicos.', instructions: 'Resuma em tópicos com fonte e data. Destaque o que mudou desde o último resumo. Ideal para uma rotina diária.', tools: true },
    writer: { label: 'Redator(a)', name: 'Redatora', purpose: 'Escrever e revisar textos: e-mails, posts, artigos e documentos no tom certo.', instructions: 'Pergunte público e tom quando não estiverem claros. Entregue uma versão final e, se útil, uma alternativa mais curta.', tools: false },
    documents: { label: 'Analista de documentos', name: 'Analista', purpose: 'Ler documentos e planilhas, extrair dados e produzir resumos, tabelas e relatórios editáveis.', instructions: 'Use o computador da abelha para converter e gerar arquivos (DOCX, PDF, CSV). Mostre os números de onde vieram.', tools: true },
    organizer: { label: 'Organizador(a)', name: 'Organizadora', purpose: 'Organizar tarefas, agenda e compromissos usando os serviços conectados.', instructions: 'Use os conectores MCP disponíveis (agenda, tarefas). Antes de criar ou alterar algo, resuma o que será feito.', tools: true },
  },
}
type Translations = typeof pt
const en: Translations = {
  title: 'Start from a template', choose: 'Template', none: 'Blank', import: 'Import template (.json)', export: 'Export as template',
  invalid: 'Invalid template file.', imported: 'Template “{{name}}” applied. Review before creating.',
  help: 'Templates fill name, purpose and instructions. No credentials, memories or permissions are copied.',
  toolsHint: 'Tip: this template works best with agent mode (recommended tools).',
  templates: {
    assistant: { label: 'Personal assistant', name: 'Assistant', purpose: 'Help day to day: answer questions, organize ideas, draft messages and remember preferences.', instructions: 'Be direct and friendly. Ask when context is missing. Save important preferences to memory when I ask.', tools: false },
    researcher: { label: 'Researcher', name: 'Researcher', purpose: 'Research the web, compare sources and deliver reports with verifiable citations.', instructions: 'Use research mode. Prefer primary and recent sources, cite each claim and point out disagreements or uncertainty.', tools: true },
    news: { label: 'News monitor', name: 'Radar', purpose: 'Follow news on chosen topics and send short periodic summaries.', instructions: 'Summarize in bullet points with source and date. Highlight what changed since the last summary. Ideal for a daily routine.', tools: true },
    writer: { label: 'Writer', name: 'Writer', purpose: 'Write and edit texts: emails, posts, articles and documents in the right tone.', instructions: 'Ask about audience and tone when unclear. Deliver a final version and, if useful, a shorter alternative.', tools: false },
    documents: { label: 'Document analyst', name: 'Analyst', purpose: 'Read documents and spreadsheets, extract data and produce summaries, tables and editable reports.', instructions: 'Use the bee’s computer to convert and generate files (DOCX, PDF, CSV). Show where numbers came from.', tools: true },
    organizer: { label: 'Organizer', name: 'Organizer', purpose: 'Organize tasks, calendar and appointments using connected services.', instructions: 'Use the available MCP connectors (calendar, tasks). Before creating or changing something, summarize what will be done.', tools: true },
  },
}
const es: Translations = {
  title: 'Empezar con una plantilla', choose: 'Plantilla', none: 'En blanco', import: 'Importar plantilla (.json)', export: 'Exportar como plantilla',
  invalid: 'Archivo de plantilla no válido.', imported: 'Plantilla “{{name}}” aplicada. Revisa antes de crear.',
  help: 'Las plantillas rellenan nombre, propósito e instrucciones. No se copian credenciales, memorias ni permisos.',
  toolsHint: 'Consejo: esta plantilla funciona mejor con el modo agente (herramientas recomendadas).',
  templates: {
    assistant: { label: 'Asistente personal', name: 'Asistente', purpose: 'Ayudar en el día a día: responder dudas, organizar ideas, redactar mensajes y recordar preferencias.', instructions: 'Sé directo y cordial. Pregunta cuando falte contexto. Guarda preferencias importantes en la memoria cuando lo pida.', tools: false },
    researcher: { label: 'Investigador(a)', name: 'Investigadora', purpose: 'Investigar en la web, comparar fuentes y entregar informes con citas verificables.', instructions: 'Usa el modo investigación. Prefiere fuentes primarias y recientes, cita cada afirmación y señala divergencias o incertidumbres.', tools: true },
    news: { label: 'Monitor de noticias', name: 'Radar', purpose: 'Seguir noticias de temas elegidos y enviar resúmenes cortos y periódicos.', instructions: 'Resume en viñetas con fuente y fecha. Destaca lo que cambió desde el último resumen. Ideal para una rutina diaria.', tools: true },
    writer: { label: 'Redactor(a)', name: 'Redactora', purpose: 'Escribir y revisar textos: correos, publicaciones, artículos y documentos con el tono adecuado.', instructions: 'Pregunta público y tono si no están claros. Entrega una versión final y, si sirve, una alternativa más corta.', tools: false },
    documents: { label: 'Analista de documentos', name: 'Analista', purpose: 'Leer documentos y hojas de cálculo, extraer datos y producir resúmenes, tablas e informes editables.', instructions: 'Usa el ordenador de la abeja para convertir y generar archivos (DOCX, PDF, CSV). Muestra de dónde salen los números.', tools: true },
    organizer: { label: 'Organizador(a)', name: 'Organizadora', purpose: 'Organizar tareas, agenda y citas usando los servicios conectados.', instructions: 'Usa los conectores MCP disponibles (agenda, tareas). Antes de crear o cambiar algo, resume lo que se hará.', tools: true },
  },
}
export const templateResources = { 'pt-BR': pt, en, es }
