// Fluxo ponta a ponta com interface compilada, API, worker e modelo simulado por HTTP.
// Primeiro acesso → criar abelha → conversar; axe-core em cada tela (390 e 1280 px,
// pt-BR/en/es). Uso: npm run build && node e2e/flow.mjs [pasta-de-evidências]
import { randomBytes, randomUUID } from 'node:crypto'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { chromium } from 'playwright'

const require = createRequire(import.meta.url)
const web = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = resolve(web, '../..')
const python = process.env.BEES_PYTHON ?? join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const out = resolve(process.argv[2] ?? join(web, 'e2e-results'))
const axeSource = readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8')
const password = 'senha descartável do e2e 12345'
const marker = 'MARCADOR-E2E-7Q'
const responsesMode = process.argv.includes('--responses')
const fixtureModel = responsesMode ? 'gpt-5.6-terra' : 'modelo-e2e'
const longConversationWidths = [390, 768, 1280, 1920]
const expectedModelRequests = 7 + longConversationWidths.length
mkdirSync(out, { recursive: true })

function freePort() {
  return new Promise((done) => { const server = createServer(); server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => done(port)) }) })
}

// Modelo OpenAI-compatível: devolve um texto com marcador e conta as gerações.
const model = { requests: 0, failNext: false, invalidArguments: false, failedTool: false, attached: false, expectedImages: [], imagesReceived: false, responsesRequests: 0, responsesContinuations: 0 }
const modelServer = createServer((request, response) => {
  let body = ''
  request.on('data', (chunk) => { body += chunk })
  request.on('end', () => {
    response.setHeader('content-type', 'application/json')
    if (request.method === 'GET' && request.url.endsWith('/models')) { response.end(JSON.stringify({ data: [{ id: fixtureModel }] })); return }
    model.requests += 1
    const responsesRoute = request.url.endsWith('/responses')
    if (responsesRoute) model.responsesRequests += 1
    const complete = (value) => {
      if (!responsesRoute) { response.end(JSON.stringify(value)); return }
      const choice = value.choices[0]
      const output = [{ type: 'reasoning', id: `rs_${model.requests}`, encrypted_content: `OPAQUE_E2E_${model.requests}`, summary: [] }]
      if (choice.message.content) output.push({ type: 'message', role: 'assistant', phase: 'final_answer', status: 'completed', content: [{ type: 'output_text', text: choice.message.content, annotations: [] }] })
      for (const call of choice.message.tool_calls ?? []) output.push({ type: 'function_call', call_id: call.id, name: call.function.name, arguments: call.function.arguments, status: 'completed' })
      const usage = value.usage ? { input_tokens: value.usage.prompt_tokens, output_tokens: value.usage.completion_tokens, total_tokens: value.usage.prompt_tokens + value.usage.completion_tokens } : null
      response.end(JSON.stringify({ status: 'completed', output, usage }))
    }
    if (model.failNext) {
      model.failNext = false
      response.statusCode = 400
      response.end(JSON.stringify({ error: { code: 'invalid_function_parameters', param: 'tools[0].function.parameters', message: 'Invalid schema: required must list every property. Bearer chave-e2e https://private.invalid/token' } }))
      return
    }
    if (model.invalidArguments) {
      model.invalidArguments = false
      complete({ choices: [{ message: { role: 'assistant', content: null, tool_calls: [{ id: 'bad-json', type: 'function', function: { name: 'publish_file', arguments: '{INVALID-PRIVATE-ARGUMENTS' } }] }, finish_reason: 'tool_calls' }] })
      return
    }
    if (model.failedTool) {
      model.failedTool = false
      complete({ choices: [{ message: { role: 'assistant', content: null, tool_calls: [{ id: 'blocked-web', type: 'function', function: { name: 'web_fetch', arguments: JSON.stringify({ url: 'http://127.0.0.1/' }) } }] }, finish_reason: 'tool_calls' }] })
      return
    }
    const payload = JSON.parse(body || '{}')
    if (responsesMode && payload.model === fixtureModel && !responsesRoute) throw new Error('GPT-5.6 enviado para Chat Completions.')
    const messages = responsesRoute ? (payload.input ?? []).map((item) => ({ role: item.type === 'function_call_output' ? 'tool' : item.role ?? 'assistant', content: item.output ?? (item.content ?? []).map((part) => part.type === 'input_image' ? { type: 'image_url', image_url: { url: part.image_url } } : { type: 'text', text: part.text }) })) : payload.messages ?? []
    if (responsesRoute && payload.input.some((item) => item.type === 'function_call_output')) {
      if (!payload.input.some((item) => item.type === 'reasoning' && item.encrypted_content.startsWith('OPAQUE_E2E_'))) throw new Error('Resultado de ferramenta sem raciocínio cifrado preservado.')
      model.responsesContinuations += 1
    }
    const textOf = (item) => typeof item.content === 'string' ? item.content : (item.content ?? []).filter((part) => part.type === 'text').map((part) => part.text).join('\n')
    if (messages.some((item) => textOf(item).includes('DADO-COLADO-E2E') && textOf(item).includes('DADO-ARRASTADO-E2E'))) model.attached = true
    const receivedImages = messages.flatMap((item) => Array.isArray(item.content) ? item.content.filter((part) => part.type === 'image_url').map((part) => part.image_url.url) : [])
    if (model.expectedImages.length === 2 && model.expectedImages.every((data) => receivedImages.includes(data))) model.imagesReceived = true
    if (payload.tools?.length && !messages.some((item) => item.role === 'tool')) {
      const call = { id: 'call-1', type: 'function', function: { name: 'publish_file', arguments: JSON.stringify({ name: 'relatorio-e2e', kind: 'markdown', content: '# Relatório\n\nConteúdo de teste.' }) } }
      complete({ choices: [{ message: { role: 'assistant', content: null, tool_calls: [call] }, finish_reason: 'tool_calls' }], usage: { prompt_tokens: 3, completion_tokens: 5 } })
      return
    }
    const last = messages.filter((item) => item.role === 'user').at(-1)
    complete({ choices: [{ message: { role: 'assistant', content: `${marker}: recebi "${last ? textOf(last).slice(0, 80) : ''}".` }, finish_reason: 'stop' }], usage: { prompt_tokens: 3, completion_tokens: 5 } })
  })
})

// Dois backends Ollama controlados: exercitam troca de destino e resposta atrasada sem baixar modelos.
const localBackends = ['a', 'b'].map((name) => {
  const state = { holdTags: false, waiting: [], writes: 0 }
  const tags = JSON.stringify({ models: [{ name: `modelo-local-${name}:latest`, size: 12 }] })
  const server = createServer((request, response) => {
    response.setHeader('content-type', 'application/json')
    if (request.method !== 'GET') { state.writes += 1; response.end('{"error":"unexpected_mutation"}'); return }
    if (request.url === '/api/version') { response.end(JSON.stringify({ version: `teste-${name}` })); return }
    if (request.url === '/api/tags') {
      if (state.holdTags) state.waiting.push(response)
      else response.end(tags)
      return
    }
    response.statusCode = 404; response.end('{}')
  })
  return { state, server, release() { state.holdTags = false; for (const response of state.waiting.splice(0)) response.end(tags) } }
})

const children = []
function start(name, args, env) {
  const log = join(out, `${name}.log`)
  writeFileSync(log, '')
  const child = spawn(python, args, { cwd: root, env: { ...process.env, ...env }, stdio: ['ignore', 'pipe', 'pipe'] })
  child.stdout.on('data', (chunk) => writeFileSync(log, chunk, { flag: 'a' }))
  child.stderr.on('data', (chunk) => writeFileSync(log, chunk, { flag: 'a' }))
  children.push(child)
  return child
}

async function waitFor(check, what, timeout = 60_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { if (await check()) return } catch { /* tenta de novo */ }
    await new Promise((done) => setTimeout(done, 250))
  }
  throw new Error(`${what} não ficou pronto em ${timeout} ms`)
}

const violations = []
async function audit(page, name) {
  await page.addScriptTag({ content: axeSource })
  const result = await page.evaluate(async () => {
    const report = await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] } })
    return report.violations.map((item) => ({ id: item.id, impact: item.impact, nodes: item.nodes.length, sample: item.nodes[0]?.target?.join(' ') }))
  })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  for (const item of result) violations.push({ page: name, ...item })
  if (overflow > 1) violations.push({ page: name, id: 'horizontal-overflow', impact: 'serious', nodes: 1, sample: `${overflow}px` })
  // A área de trabalho ocupa a janela: nada rola por fora da lista de mensagens (sem faixa vazia embaixo).
  const shell = await page.evaluate(() => {
    const root = document.querySelector('[data-shell]')
    return root ? { shifted: root.scrollTop + document.documentElement.scrollTop, extra: document.documentElement.scrollHeight - document.documentElement.clientHeight } : null
  })
  if (shell && (shell.shifted > 0 || shell.extra > 1)) violations.push({ page: name, id: 'page-scroll', impact: 'serious', nodes: 1, sample: JSON.stringify(shell) })
  await page.screenshot({ path: join(out, `${name}.png`), fullPage: true })
}

async function auditWidths(page, name) {
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.waitForTimeout(150)
    await audit(page, `${name}-${width}`)
  }
}

async function checkPanelKeyboard(page) {
  const tablist = page.getByRole('tablist', { name: 'Painel lateral' })
  const activity = tablist.getByRole('tab', { name: 'Atividade', exact: true })
  const expectTab = async (name) => {
    const tab = tablist.getByRole('tab', { name, exact: true, selected: true })
    const panel = page.getByRole('tabpanel', { name, exact: true })
    await panel.waitFor()
    if (!await tab.evaluate((element) => element === document.activeElement)
      || await tab.getAttribute('aria-controls') !== await panel.getAttribute('id')) {
      throw new Error(`A aba ${name} perdeu o foco ou o vínculo com o painel.`)
    }
  }
  await activity.focus()
  await page.keyboard.press('ArrowRight')
  await expectTab('Computador')
  await page.keyboard.press('ArrowLeft')
  await expectTab('Atividade')
  await page.keyboard.press('End')
  await expectTab('Computador')
  await page.keyboard.press('Home')
  await expectTab('Atividade')
  await page.keyboard.press('Tab')
  if (await tablist.evaluate((element) => element.contains(document.activeElement))) throw new Error('Tab ficou preso na lista de abas.')
  await page.keyboard.press('Shift+Tab')
  await expectTab('Atividade')
  await tablist.getByRole('tab', { name: 'Computador', exact: true }).click()
  await expectTab('Computador')
  await activity.click()
  await expectTab('Atividade')
}

async function checkTaskDetailsKeyboard(page, trigger, reason, context) {
  const detail = page.getByRole('dialog', { name: 'Atividade', exact: true })
  await trigger.waitFor()
  if (await trigger.getAttribute('aria-haspopup') !== 'dialog') throw new Error(`O botão de ${context} não anuncia o diálogo.`)
  await trigger.press('Enter')
  await detail.getByText(reason, { exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await detail.waitFor({ state: 'hidden' })
  await waitFor(() => trigger.evaluate((element) => element === document.activeElement), `foco em ${context} após Escape`, 5_000)
  await page.keyboard.press('Space')
  await detail.getByRole('button', { name: 'Fechar painel', exact: true }).press('Enter')
  await detail.waitFor({ state: 'hidden' })
  await waitFor(() => trigger.evaluate((element) => element === document.activeElement), `foco em ${context} após Fechar painel`, 5_000)
}

async function checkSafetyPanelKeyboard(page, width, scope = page) {
  const trigger = scope.getByRole('button', { name: 'Executando normalmente', exact: true }).filter({ visible: true })
  const triggerWithPanelOpen = scope.getByRole('button', { name: 'Executando normalmente', exact: true, includeHidden: true }).filter({ visible: true })
  const panel = page.getByRole('dialog', { name: 'Execução', exact: true })
  if (await trigger.getAttribute('aria-haspopup') !== 'dialog') throw new Error('O controle de execução não anuncia o diálogo.')
  const close = panel.getByRole('button', { name: 'Fechar painel', exact: true })
  const expectFocus = async (target, message) => {
    try {
      await waitFor(() => target.evaluate((element) => element === document.activeElement, undefined, { timeout: 500 }), message, 5_000)
    } catch (error) {
      const state = await page.evaluate(() => ({
        active: document.activeElement?.getAttribute('aria-label') ?? document.activeElement?.tagName,
        dialogs: [...document.querySelectorAll('[role="dialog"]')].map((dialog) => ({
          title: document.getElementById(dialog.getAttribute('aria-labelledby'))?.textContent,
          hidden: dialog.closest('[aria-hidden="true"]') !== null,
        })),
      }))
      throw new Error(`${error.message}. Estado do foco: ${JSON.stringify(state)}`, { cause: error })
    }
  }
  await trigger.press('Enter')
  await expectFocus(close, `foco inicial em Execução a ${width} px`)
  if (await triggerWithPanelOpen.getAttribute('aria-expanded') !== 'true') throw new Error('O controle de execução não anuncia o painel aberto.')
  await page.keyboard.press('Escape')
  await panel.waitFor({ state: 'hidden' })
  await expectFocus(trigger, `foco em Execução após Escape a ${width} px`)
  await page.keyboard.press('Space')
  await expectFocus(close, `foco inicial em Execução a ${width} px`)
  await page.keyboard.press('Enter')
  await panel.waitFor({ state: 'hidden' })
  await expectFocus(trigger, `foco em Execução após Fechar painel a ${width} px`)
  if (await trigger.getAttribute('aria-expanded') !== 'false') throw new Error('O controle de execução não anuncia o painel fechado.')
}

async function closeDesktopPanelWithKeyboard(page, target) {
  const panel = page.getByRole('complementary', { name: 'Painel lateral', exact: true })
  await panel.getByRole('button', { name: 'Fechar painel', exact: true }).press('Enter')
  await panel.waitFor({ state: 'hidden' })
  await waitFor(() => target.evaluate((element) => element === document.activeElement), 'foco após fechar o painel lateral no desktop', 5_000)
}

async function checkSettingsKeyboard(page) {
  const name = 'Configurações da abelha'
  const header = page.locator('main > header').getByRole('button', { name, exact: true })
  const desktop = page.getByRole('complementary', { name: 'Suas abelhas', exact: true }).getByRole('button', { name, exact: true })
  const menu = page.getByRole('button', { name: 'Abrir menu', exact: true })
  const drawer = page.getByRole('dialog', { name: 'bees.', exact: true })
  const heading = page.locator('#settings-heading')
  const back = page.getByRole('button', { name: 'Voltar à conversa', exact: true })
  const checkRoundTrip = async (opener, returnTarget, context) => {
    await opener.press('Enter')
    await heading.waitFor()
    await waitFor(() => heading.evaluate((element) => element === document.activeElement), `foco na seção ao abrir ${context}`, 5_000)
    await back.press('Enter')
    await heading.waitFor({ state: 'hidden' })
    await waitFor(() => returnTarget.evaluate((element) => element === document.activeElement), `foco ao voltar de ${context}`, 5_000)
  }
  await page.setViewportSize({ width: 390, height: 900 })
  await checkRoundTrip(header, header, 'cabeçalho móvel')
  await menu.press('Enter')
  await checkRoundTrip(drawer.getByRole('button', { name, exact: true }), menu, 'menu móvel')
  await page.setViewportSize({ width: 1280, height: 900 })
  await checkRoundTrip(desktop, desktop, 'barra de abelhas no desktop')
  await checkRoundTrip(header, header, 'cabeçalho desktop')
}

const agentTurnLocales = {
  'pt-BR': { composer: 'Mensagem para Abelha Agente…', decision: 'Decisões pendentes', allow: 'Permitir uma vez', download: /^Baixar relatorio-e2e/ },
  en: { composer: 'Message Abelha Agente…', decision: 'Pending decisions', allow: 'Allow once', download: /^Download relatorio-e2e/ },
  es: { composer: 'Mensaje para Abelha Agente…', decision: 'Decisiones pendientes', allow: 'Permitir una vez', download: /^Descargar relatorio-e2e/ },
}

async function auditLocalizedAgentTurn(page, log, name, checkTurn) {
  const languageSelect = page.locator('.language-select select').first()
  // Na largura móvel, o seletor fica dentro do menu recolhido.
  for (const [language, labels] of Object.entries(agentTurnLocales)) {
    await page.setViewportSize({ width: 1280, height: 900 })
    await languageSelect.selectOption(language)
    await page.locator(`html[lang="${language}"]`).waitFor({ state: 'attached' })
    for (const width of [1280, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await page.getByRole('textbox', { name: labels.composer, exact: true }).waitFor()
      await checkTurn(log, labels)
      await audit(page, `${name}-${language}-${width}`)
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 })
  await languageSelect.selectOption('pt-BR')
}

async function conversationLayout(page, position) {
  return page.getByRole('log').evaluate((list, where) => {
    const messages = list.firstElementChild
    const input = document.querySelector('#chat-input')
    const composer = input.closest('form').getBoundingClientRect()
    const shell = document.querySelector('[data-shell]')
    const root = document.scrollingElement
    const range = list.scrollHeight - list.clientHeight
    if (where === 'top') list.scrollTop = 0
    if (where === 'bottom') list.scrollTop = range
    const listBounds = list.getBoundingClientRect()
    const oldest = messages.firstElementChild?.getBoundingClientRect()
    const newest = messages.lastElementChild?.getBoundingClientRect()
    return {
      range, scrollTop: list.scrollTop,
      oldestVisible: oldest && oldest.top >= listBounds.top - 1 && oldest.bottom <= listBounds.bottom + 1,
      newestVisible: newest && newest.top >= listBounds.top - 1 && newest.bottom <= listBounds.bottom + 1,
      pageFixed: root.scrollHeight - root.clientHeight <= 1 && root.scrollTop === 0 && shell.scrollTop === 0,
      composerVisible: composer.top >= listBounds.bottom - 1 && composer.bottom <= window.innerHeight + 1
        && composer.left >= 0 && composer.right <= window.innerWidth + 1,
      composerBottom: composer.bottom,
      composerFocused: document.activeElement === input,
    }
  }, position ?? null)
}

async function auditLongConversation(page, input) {
  // Injeta só o histórico antigo na leitura. Envio e novas respostas percorrem API e modelo HTTP.
  const history = Array.from({ length: 50 }, (_, index) => ({
    id: `historico-e2e-${index}`,
    role: index % 2 === 0 ? 'user' : 'assistant',
    content: `Mensagem de histórico E2E ${index + 1}.`,
    created_at: new Date(Date.UTC(2025, 0, 1, 0, index)).toISOString(),
  }))
  const messagesUrl = /\/api\/v1\/agents\/[^/]+\/messages\?/
  const prependHistory = async (route) => {
    const response = await route.fetch()
    if (!response.ok()) { await route.fulfill({ response }); return }
    const conversation = await response.json()
    await route.fulfill({ json: { ...conversation, messages: [...history, ...conversation.messages] } })
  }
  await page.route(messagesUrl, prependHistory)
  try {
    await page.reload()
    const log = page.getByRole('log')
    await log.getByText(history[0].content, { exact: false }).waitFor()
    if (await log.getByText(/Mensagem de histórico E2E \d+\./).count() !== history.length) {
      throw new Error('O histórico da conversa não renderizou as 50 mensagens.')
    }
    for (const width of longConversationWidths) {
      await page.setViewportSize({ width, height: 900 })
      await input.focus()
      await input.press('Shift+Tab')
      if (!await log.evaluate((list) => list === document.activeElement)) {
        throw new Error(`Teclado não alcançou o histórico em ${width}px.`)
      }
      await page.keyboard.press('Home')
      await waitFor(async () => (await conversationLayout(page)).scrollTop <= 1, `início do histórico em ${width}px`, 5_000)
      const top = await conversationLayout(page)
      if (top.range <= 0 || !top.oldestVisible || !top.pageFixed || !top.composerVisible) {
        throw new Error(`Histórico longo em ${width}px: topo ou compositor fora da área visível: ${JSON.stringify(top)}`)
      }
      await page.keyboard.press('End')
      await waitFor(async () => {
        const layout = await conversationLayout(page)
        return layout.range - layout.scrollTop <= 1
      }, `fim do histórico em ${width}px`, 5_000)
      const bottom = await conversationLayout(page)
      if (bottom.range - bottom.scrollTop > 1 || !bottom.newestVisible || !bottom.pageFixed || !bottom.composerVisible) {
        throw new Error(`Histórico longo em ${width}px: fim ou compositor fora da área visível: ${JSON.stringify(bottom)}`)
      }

      const prompt = `Layout ${width}`
      const before = model.requests
      await input.fill(prompt)
      await input.press('End')
      await input.press('Shift+Enter')
      if (await input.inputValue() !== `${prompt}\n` || model.requests !== before) {
        throw new Error(`Shift+Enter alterou o fluxo de envio em ${width}px.`)
      }
      await input.press('a')
      const [request] = await Promise.all([
        page.waitForRequest((entry) => entry.method() === 'POST' && /\/api\/v1\/agents\/[^/]+\/chat$/.test(new URL(entry.url()).pathname)),
        input.press('Enter'),
      ])
      if (request.postDataJSON().content !== `${prompt}\na`) {
        throw new Error(`Enter não enviou a mensagem com a quebra de linha em ${width}px.`)
      }
      await log.getByText(new RegExp(`${marker}.*${prompt}`)).waitFor({ timeout: 60_000 })
      await waitFor(async () => {
        const layout = await conversationLayout(page)
        return layout.range - layout.scrollTop <= 1 && layout.newestVisible && layout.composerFocused
      }, `última resposta e foco em ${width}px`, 5_000)
      const after = await conversationLayout(page)
      if (!after.pageFixed || !after.composerVisible || Math.abs(after.composerBottom - bottom.composerBottom) > 1
        || await input.inputValue() !== '' || model.requests !== before + 1) {
        throw new Error(`Histórico longo em ${width}px: envio deslocou o compositor ou não concluiu corretamente: ${JSON.stringify(after)}`)
      }
      await audit(page, `5-conversa-longa-${width}`)
    }
    // Mantém a verificação original de janela baixa com mensagens renderizadas.
    await page.setViewportSize({ width: 1280, height: 300 })
    for (const position of ['top', 'bottom']) {
      const layout = await conversationLayout(page, position)
      if (layout.range <= 0 || !layout.pageFixed || !layout.composerVisible) {
        throw new Error(`Histórico longo em janela baixa (${position}): ${JSON.stringify(layout)}`)
      }
    }
  } finally {
    await page.unroute(messagesUrl, prependHistory)
    await page.setViewportSize({ width: 1280, height: 900 })
  }
}

async function selectSettings(page, value) {
  const option = page.locator(`#settings-section option[value="${value}"]`)
  const label = await option.textContent()
  if (page.viewportSize().width >= 768) {
    await page.locator('[data-settings] nav').getByRole('button', { name: label, exact: true }).click()
  } else {
    await page.getByLabel('Seção das configurações').selectOption(value)
  }
  await page.getByRole('region', { name: label, exact: true }).waitFor()
  await page.waitForTimeout(150)
  await waitFor(async () => await page.locator('[data-settings] .loading-notice, [data-settings] [aria-busy="true"]').count() === 0, `configurações ${value}`)
}

let browser
const data = mkdtempSync(join(tmpdir(), 'bees-e2e-'))
try {
  const [apiPort, modelPort, localPortA, localPortB] = await Promise.all([freePort(), freePort(), freePort(), freePort()])
  await new Promise((done) => modelServer.listen(modelPort, '127.0.0.1', done))
  await Promise.all(localBackends.map(({ server }, index) => new Promise((done) => server.listen(index ? localPortB : localPortA, '127.0.0.1', done))))
  const env = { BEES_VAULT_KEY: randomBytes(32).toString('base64url') + '=', BEES_DATA_DIR: data, BEES_PORT: String(apiPort), BEES_WEB_DIST: join(web, 'dist') }
  const origin = `http://127.0.0.1:${apiPort}`
  start('api', ['-c', 'from bees_api.cli import main; main()'], env)
  await waitFor(async () => (await fetch(`${origin}/api/v1/health`)).ok, 'API')
  const bootstrap = spawnSync(python, ['-c', 'from bees_api.bootstrap_cli import main; main()', 'bootstrap'], { cwd: root, env: { ...process.env, ...env }, encoding: 'utf8' })
  const code = bootstrap.stdout.match(/^[A-Za-z0-9_-]{43}$/m)?.[0]
  if (!code) throw new Error('Código de primeiro acesso não emitido.')
  start('worker', ['-c', 'from bees_worker.cli import main; main()'], env)

  browser = await chromium.launch()
  // Autoverificação: o axe precisa acusar uma falha conhecida antes de valer como portão.
  const probe = await browser.newPage()
  await probe.setContent('<html lang="pt-BR"><title>x</title><main><img src="data:,"><button></button></main></html>')
  await probe.addScriptTag({ content: axeSource })
  const known = await probe.evaluate(async () => (await window.axe.run()).violations.map((item) => item.id))
  if (!known.includes('image-alt') || !known.includes('button-name')) throw new Error(`axe não detectou falhas conhecidas: ${known}`)
  await probe.close()
  // bypassCSP só para injetar o axe; a política real continua testada pela API.
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, bypassCSP: true })
  await page.goto(origin)
  await page.getByRole('heading', { name: 'Vamos preparar seu acesso.' }).waitFor()
  await auditWidths(page, '1-primeiro-acesso')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByText('Opção avançada').click()
  await page.getByLabel('Código de primeiro acesso').fill(code)
  await page.getByLabel('Seu nome').fill('Pessoa E2E')
  await page.getByLabel(/^Senha/).fill(password)
  await page.getByLabel('Repita a senha').fill(password)
  await page.getByRole('button', { name: 'Criar meu acesso' }).click()

  await page.getByRole('heading', { name: 'Crie sua abelha.' }).waitFor()
  await auditWidths(page, '2-criar-abelha')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByLabel('Provedor').selectOption('custom')
  await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${modelPort}/v1`)
  await page.getByLabel('Chave da API').fill('E2E-CHAVE-SINTETICA-SEM-ACESSO-REAL')
  await page.getByLabel(/^Digitar outro modelo/).check()
  await page.getByLabel(/^Identificador do modelo|^Modelo/).last().fill(fixtureModel)
  await page.getByLabel('Nome da abelha').fill('Abelha E2E')
  await page.getByRole('textbox', { name: 'Propósito' }).fill('Responder perguntas de teste.')
  await page.getByRole('button', { name: 'Criar abelha' }).click()

  const input = page.locator('#chat-input')
  await input.waitFor({ timeout: 30_000 })
  await auditWidths(page, '3-conversa-vazia')
  await page.setViewportSize({ width: 1280, height: 900 })
  await input.fill('Olá, abelha de teste')
  await page.getByRole('button', { name: 'Enviar' }).click()
  await page.getByText(marker).first().waitFor({ timeout: 60_000 })
  await auditWidths(page, '4-conversa-respondida')
  await page.setViewportSize({ width: 1280, height: 900 })
  for (const language of ['en', 'es']) {
    await page.locator('.language-select select').selectOption(language)
    await page.waitForTimeout(300)
    await audit(page, `5-conversa-${language}`)
  }
  await page.locator('.language-select select').first().selectOption('pt-BR')
  await auditLongConversation(page, input)
  // Modo agente: a pessoa autoriza a publicação e baixa o arquivo no próprio turno.
  const before = model.requests
  await page.getByRole('button', { name: 'Nova abelha' }).click()
  await page.getByRole('heading', { name: 'Crie sua abelha.' }).waitFor()
  await page.getByLabel('Provedor').selectOption('custom')
  await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${modelPort}/v1`)
  await page.getByLabel('Credencial da sua conta Bees').selectOption({ label: 'Chave salva 1' })
  if (await page.locator('#api-key').count()) throw new Error('Reuso da conta exigiu redigitar a chave.')
  await page.getByLabel(/^Digitar outro modelo/).check()
  await page.getByLabel(/^Identificador do modelo|^Modelo/).last().fill(responsesMode ? fixtureModel : 'modelo-agente')
  await page.getByLabel(/declara suporte a chamadas de ferramentas/).evaluate((element) => { if (!element.checked) element.click() })
  await page.getByLabel(/^Este modelo aceita imagens/).evaluate((element) => { if (!element.checked) element.click() })
  await page.getByLabel('Nome da abelha').fill('Abelha Agente')
  await page.getByRole('textbox', { name: 'Propósito' }).fill('Gerar relatórios de teste.')
  const [createdAgent] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/agents'),
    page.getByRole('button', { name: 'Criar abelha' }).click(),
  ])
  if (createdAgent.status() !== 201) throw new Error(`Criação da abelha agente falhou: HTTP ${createdAgent.status()}.`)
  const agent = await createdAgent.json()
  await page.locator('#chat-input').waitFor({ timeout: 30_000 })
  await page.getByText('Pesquise as novidades', { exact: false }).first().waitFor({ timeout: 30_000 })
  // Prepara a regra pela API real. A decisão será tomada somente na conversa.
  const agentUrl = `${origin}/api/v1/agents/${agent.id}`
  const authStatus = await page.request.get(`${origin}/api/v1/auth/status`)
  const { csrf_token: csrfToken } = await authStatus.json()
  const policy = await page.request.post(`${agentUrl}/policies`, {
    headers: { Origin: origin, 'X-Bees-CSRF': csrfToken },
    data: { client_request_id: randomUUID(), name: 'Confirmar publicação E2E', effect: 'ask', scope: { tool_name: 'deliverable.publish', action: 'publish' } },
  })
  if (policy.status() !== 201) throw new Error(`Regra de publicação não criada: HTTP ${policy.status()}.`)
  const attachmentDraft = page.locator('#chat-input')
  const attachmentPicker = page.locator('input[type="file"]')
  const attachButton = page.getByRole('button', { name: /^Anexar arquivo/ })
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await attachmentDraft.fill('Rascunho com anexos')
    await waitFor(() => attachButton.isEnabled(), 'anexos prontos para verificar o foco')
    await attachmentPicker.setInputFiles([
      { name: 'remover-enter.txt', mimeType: 'text/plain', buffer: Buffer.from('Enter') },
      { name: 'remover-espaco.txt', mimeType: 'text/plain', buffer: Buffer.from('Espaço') },
    ])
    const removeWithEnter = page.getByRole('button', { name: 'Remover remover-enter.txt', exact: true })
    const removeWithSpace = page.getByRole('button', { name: 'Remover remover-espaco.txt', exact: true })
    await removeWithSpace.waitFor()
    for (const [remove, key] of [[removeWithEnter, 'Enter'], [removeWithSpace, 'Space']]) {
      await remove.press(key)
      await remove.waitFor({ state: 'detached' })
      if (!await attachmentDraft.evaluate((element) => element === document.activeElement)) {
        throw new Error(`O rascunho não recuperou o foco após remover anexo com ${key} a ${width} px.`)
      }
      if (await attachmentDraft.inputValue() !== 'Rascunho com anexos') {
        throw new Error(`O texto do rascunho mudou após remover anexo a ${width} px.`)
      }
    }
  }
  await attachmentDraft.fill('')
  // Eventos reais de DOM com arquivos de DataTransfer: colagem e drop na lista, com upload pela API.
  await page.locator('#chat-input').evaluate((input) => {
    const clipboardData = new DataTransfer()
    clipboardData.items.add(new File(['DADO-COLADO-E2E'], 'colado.txt', { type: 'text/plain' }))
    input.dispatchEvent(new ClipboardEvent('paste', { clipboardData, bubbles: true, cancelable: true }))
  })
  await page.getByRole('button', { name: 'Remover colado.txt', exact: true }).waitFor()
  const dropped = await page.evaluateHandle(() => {
    const dataTransfer = new DataTransfer()
    dataTransfer.items.add(new File(['DADO-ARRASTADO-E2E'], 'arrastado.md', { type: 'text/markdown' }))
    return dataTransfer
  })
  await page.getByRole('log').dispatchEvent('dragenter', { dataTransfer: dropped })
  await page.getByText('Solte os arquivos para anexar', { exact: true }).waitFor()
  await audit(page, '6a-arrastando-anexos')
  await page.getByRole('log').dispatchEvent('drop', { dataTransfer: dropped })
  await dropped.dispose()
  await page.getByRole('button', { name: 'Remover arrastado.md', exact: true }).waitFor()
  // PNG colado e JPEG arrastado devem ser enviados como blocos visuais, com bytes exatos.
  model.expectedImages = await page.locator('#chat-input').evaluate((input) => {
    const canvas = document.createElement('canvas'); canvas.width = 240; canvas.height = 80
    const context = canvas.getContext('2d'); context.fillStyle = 'white'; context.fillRect(0, 0, 240, 80); context.fillStyle = 'black'; context.font = '24px sans-serif'; context.fillText('BEES PRINT 221', 10, 40)
    const images = ['image/png', 'image/jpeg'].map((mime) => canvas.toDataURL(mime))
    images.forEach((data, index) => {
      const bytes = Uint8Array.from(atob(data.split(',')[1]), (char) => char.charCodeAt(0))
      const transfer = new DataTransfer(); transfer.items.add(new File([bytes], index ? 'arrastado.jpg' : 'colado.png', { type: index ? 'image/jpeg' : 'image/png' }))
      input.dispatchEvent(index ? new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true }) : new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }))
    })
    return images
  })
  await page.getByRole('button', { name: 'Remover colado.png', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Remover arrastado.jpg', exact: true }).waitFor()
  await page.getByText('As imagens serão enviadas ao modelo configurado com esta mensagem.', { exact: true }).waitFor()
  await page.locator('#chat-input').fill('Gere um relatório de teste')
  const [submitted] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'POST' && response.url() === `${agentUrl}/conversations/${agent.conversation_id}/agent-messages`),
    page.locator('#chat-input').press('Enter'),
  ])
  if (!submitted.ok()) throw new Error(`Envio ao agente falhou: HTTP ${submitted.status()}.`)
  const { task_id: taskId } = await submitted.json()
  const agentLog = page.getByRole('log', { name: 'Abelha Agente' })
  const decision = agentLog.getByRole('region', { name: 'Decisões pendentes' })
  const inlineDownload = agentLog.getByRole('button', { name: /Baixar relatorio-e2e/ })
  await decision.getByRole('heading', { name: 'Publicar arquivo de entrega' }).waitFor({ timeout: 90_000 })
  await agentLog.getByText(/Gere um relatório de teste/).waitFor()
  const unpublished = await page.request.get(`${agentUrl}/tasks/${taskId}/artifacts`)
  if (!unpublished.ok() || (await unpublished.json()).artifacts.length !== 0 || model.requests !== before + 1) {
    throw new Error('A publicação ou a continuação do modelo avançou antes da decisão.')
  }
  await auditLocalizedAgentTurn(page, agentLog, '6b-aprovacao-pendente', (log, labels) =>
    log.getByRole('region', { name: labels.decision, exact: true })
      .getByRole('button', { name: labels.allow, exact: true }).waitFor())
  const [decided] = await Promise.all([
    page.waitForResponse((response) => response.request().method() === 'POST' && response.url().startsWith(`${agentUrl}/agent-tool-approvals/`) && response.url().endsWith('/decision')),
    decision.getByRole('button', { name: 'Permitir uma vez', exact: true }).click(),
  ])
  if (!decided.ok()) throw new Error(`Decisão de publicação falhou: HTTP ${decided.status()}.`)
  const { approval, receipt } = await decided.json()
  if (receipt.confirmed !== true || approval.task_id !== taskId || approval.decision !== 'allow_once') {
    throw new Error('A API não confirmou a decisão pontual da tarefa enviada.')
  }
  await inlineDownload.waitFor({ timeout: 90_000 })
  const stored = await page.request.get(`${agentUrl}/agent-tool-approvals/${approval.id}`)
  if (!stored.ok() || !(await stored.json()).consumed_at) throw new Error('O worker não consumiu a autorização pontual.')
  const [downloaded] = await Promise.all([page.waitForEvent('download'), inlineDownload.click()])
  const downloadedPath = await downloaded.path()
  if (!downloadedPath || readFileSync(downloadedPath, 'utf8') !== '# Relatório\n\nConteúdo de teste.') throw new Error('Download inline não preservou os bytes da versão publicada.')
  if (!downloaded.suggestedFilename().startsWith('relatorio-e2e')) throw new Error('Download inline perdeu o nome registrado.')
  await page.getByRole('status').filter({ hasText: 'Download enviado ao navegador.' }).waitFor()
  await agentLog.getByText(new RegExp(`${marker}.*Gere um relatório de teste`)).waitFor({ timeout: 60_000 })
  const history = await page.request.get(`${agentUrl}/messages?conversation_id=${agent.conversation_id}`)
  if (!history.ok() || !(await history.json()).messages.some((message) =>
    message.role === 'assistant' && message.agent_task_id === taskId && message.content?.includes(marker))) {
    throw new Error('O histórico não vinculou a resposta publicada à tarefa do agente.')
  }
  await page.getByText('Usou 1 ferramenta').waitFor()
  if (!model.attached) throw new Error('Anexos colado e arrastado não chegaram juntos ao modelo.')
  if (!model.imagesReceived) throw new Error('PNG/JPEG não chegaram ao modelo como imagens com bytes exatos.')
  await auditLocalizedAgentTurn(page, agentLog, '6-agente-arquivo', (log, labels) =>
    log.getByRole('button', { name: labels.download }).waitFor())
  const desktopActivity = page.getByRole('button', { name: 'Atividade', exact: true })
  const desktopComputer = page.getByRole('button', { name: 'Computador', exact: true })
  const desktopTabs = page.getByRole('tablist', { name: 'Painel lateral' })
  await desktopActivity.press('Enter')
  await page.getByRole('tabpanel').getByText('Gere um relatório de teste').waitFor()
  await checkPanelKeyboard(page)
  await audit(page, '7-atividade-lateral')
  await desktopTabs.getByRole('tab', { name: 'Atividade', exact: true }).press('ArrowRight')
  await desktopTabs.getByRole('tab', { name: 'Computador', selected: true }).waitFor()
  await closeDesktopPanelWithKeyboard(page, desktopActivity)
  await desktopComputer.press('Space')
  await page.getByText(/Nenhum runtime de computador|computador desta abelha está desligado/).first().waitFor()
  await audit(page, '7-computador-lateral')
  await desktopTabs.getByRole('tab', { name: 'Computador', exact: true }).press('ArrowLeft')
  await desktopTabs.getByRole('tab', { name: 'Atividade', selected: true }).waitFor()
  await closeDesktopPanelWithKeyboard(page, desktopComputer)

  // Simula apenas a leitura de uma etapa de computador, sem iniciar um runtime.
  const delegationsUrl = `${agentUrl}/delegations?conversation_id=${agent.conversation_id}&offset=0&limit=100`
  const reportComputerActivity = async (route) => {
    const response = await route.fetch()
    const payload = await response.json()
    const task = payload.delegations.find((item) => item.id === taskId).task
    task.status = 'running'
    task.agent_steps = [{ tool: 'computer_screenshot', status: null, code: null, at: task.updated_at }]
    await route.fulfill({ response, json: payload })
  }
  const composer = page.locator('#chat-input')
  await composer.focus()
  await page.route(delegationsUrl, reportComputerActivity)
  try {
    await desktopTabs.getByRole('tab', { name: 'Computador', selected: true }).waitFor()
    if (!await composer.evaluate((element) => element === document.activeElement)) throw new Error('A abertura automática do painel tirou o foco da conversa.')
    await closeDesktopPanelWithKeyboard(page, desktopComputer)
  } finally {
    await page.unroute(delegationsUrl, reportComputerActivity)
  }
  await checkSafetyPanelKeyboard(page, 1280)
  await page.setViewportSize({ width: 390, height: 900 })
  const menuButton = page.getByRole('button', { name: 'Abrir menu', exact: true })
  const beeMenu = page.getByRole('dialog', { name: 'bees.', exact: true })
  if (await menuButton.getAttribute('aria-haspopup') !== 'dialog') throw new Error('O botão Abrir menu não anuncia o diálogo.')
  await menuButton.press('Enter')
  await beeMenu.waitFor()
  const hiddenMenuButton = page.getByRole('button', { name: 'Abrir menu', exact: true, includeHidden: true })
  if (await hiddenMenuButton.getAttribute('aria-expanded') !== 'true') throw new Error('O botão Abrir menu não registra a gaveta aberta.')
  await checkSafetyPanelKeyboard(page, 390, beeMenu)
  await beeMenu.waitFor()
  await page.keyboard.press('Escape')
  await beeMenu.waitFor({ state: 'hidden' })
  await waitFor(() => menuButton.evaluate((element) => element === document.activeElement), 'foco em Abrir menu após Escape', 5_000)
  await page.keyboard.press('Space')
  await beeMenu.getByRole('button', { name: 'Fechar painel', exact: true }).press('Enter')
  await beeMenu.waitFor({ state: 'hidden' })
  await waitFor(() => menuButton.evaluate((element) => element === document.activeElement), 'foco em Abrir menu após Fechar painel', 5_000)
  if (await menuButton.getAttribute('aria-expanded') !== 'false') throw new Error('O botão Abrir menu não anuncia a gaveta fechada.')
  const mobileActivity = page.getByRole('button', { name: 'Atividade', exact: true })
  const mobileComputer = page.getByRole('button', { name: 'Computador', exact: true })
  const mobilePanel = page.getByRole('dialog', { name: 'Painel lateral' })
  await mobileActivity.press('Enter')
  await checkPanelKeyboard(page)
  await page.keyboard.press('ArrowRight')
  await mobilePanel.getByRole('tab', { name: 'Computador', selected: true }).waitFor()
  await page.keyboard.press('Escape')
  await mobilePanel.waitFor({ state: 'hidden' })
  await waitFor(() => mobileActivity.evaluate((element) => element === document.activeElement), 'foco no botão Atividade após Escape', 5_000)
  await mobileComputer.press('Space')
  await mobilePanel.getByRole('tab', { name: 'Computador', selected: true }).waitFor()
  await mobilePanel.getByRole('tab', { name: 'Computador', selected: true }).press('ArrowLeft')
  await mobilePanel.getByRole('tab', { name: 'Atividade', selected: true }).waitFor()
  await mobilePanel.getByRole('button', { name: 'Fechar painel' }).press('Enter')
  await mobilePanel.waitFor({ state: 'hidden' })
  await waitFor(() => mobileComputer.evaluate((element) => element === document.activeElement), 'foco no botão Computador após fechar', 5_000)
  const filesButton = page.getByRole('button', { name: 'Arquivos da conversa', exact: true })
  const filesPanel = page.getByRole('dialog', { name: 'Arquivos da conversa', exact: true })
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    await filesButton.press('Enter')
    await filesPanel.waitFor()
    await page.keyboard.press('Escape')
    await filesPanel.waitFor({ state: 'hidden' })
    await waitFor(() => filesButton.evaluate((element) => element === document.activeElement), `foco em Arquivos após Escape a ${width} px`, 5_000)
    await page.keyboard.press('Space')
    await filesPanel.getByRole('button', { name: 'Fechar painel' }).press('Enter')
    await filesPanel.waitFor({ state: 'hidden' })
    await waitFor(() => filesButton.evaluate((element) => element === document.activeElement), `foco em Arquivos após Fechar painel a ${width} px`, 5_000)
  }
  await checkSettingsKeyboard(page)
  await page.getByRole('button', { name: 'Configurações da abelha' }).first().click()
  await page.getByRole('button', { name: 'Voltar à conversa' }).waitFor()
  // Exercita o caminho real de edição, sem gerar: salvar perfil e modelo mantém a abelha e a conversa.
  await page.getByRole('textbox', { name: 'Propósito', exact: true }).fill('Perfil salvo nas configurações novas.')
  await page.getByRole('button', { name: 'Salvar perfil', exact: true }).click()
  await page.getByRole('status').filter({ hasText: 'Perfil atualizado.' }).waitFor()
  await selectSettings(page, 'model')
  if (await page.locator('#settings-heading').evaluate((element) => element !== document.activeElement)) throw new Error('Navegação não levou o foco ao título da seção.')
  await page.keyboard.press('Tab')
  if (await page.locator('#provider-kind').evaluate((element) => element !== document.activeElement)) throw new Error('Teclado não alcançou o primeiro campo do modelo.')
  await page.getByLabel(/^Digitar outro modelo/).check()
  await page.getByRole('textbox', { name: 'Modelo', exact: true }).fill(fixtureModel)
  await page.getByRole('button', { name: /^Testar conexão/ }).click()
  await page.getByRole('status').filter({ hasText: 'Modelo encontrado' }).waitFor()
  await page.getByRole('button', { name: 'Salvar e usar este modelo', exact: true }).click()
  await page.getByRole('status').filter({ hasText: 'Modelo atualizado.' }).waitFor()
  await selectSettings(page, 'profile')
  if (await page.getByRole('textbox', { name: 'Propósito', exact: true }).inputValue() !== 'Perfil salvo nas configurações novas.') throw new Error('Perfil salvo não foi preservado.')

  // A revisão de uma revogação continua explícita; audita também o botão destrutivo em hover, sem confirmar.
  await selectSettings(page, 'tools')
  await page.getByRole('button', { name: 'Revogar uso', exact: true }).first().click()
  await page.getByRole('button', { name: 'Confirmar alteração', exact: true }).hover()
  await auditWidths(page, '8-configuracoes-confirmacao-clara')
  await page.locator('.tool-confirmation').getByRole('button', { name: 'Cancelar', exact: true }).click()
  const sections = await page.locator('#settings-section option').evaluateAll((items) => items.map((item) => item.value))
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 })
    for (const section of sections) {
      await selectSettings(page, section)
      await audit(page, `8-configuracoes-${section}-${width}`)
    }
  }
  // O seletor móvel permanece focado, permitindo percorrer as seções pelo teclado nativo.
  await page.locator('#settings-section').focus()
  await selectSettings(page, 'model')
  if (await page.locator('#settings-section').evaluate((element) => element !== document.activeElement)) throw new Error('Seletor móvel perdeu o foco ao trocar seção.')
  await page.setViewportSize({ width: 1280, height: 900 })
  for (const language of ['en', 'es']) {
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.locator('.language-select select').first().selectOption(language)
    await page.waitForTimeout(150)
    await auditWidths(page, `9-configuracoes-modelo-${language}`)
  }
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.locator('.language-select select').first().selectOption('pt-BR')
  await page.locator('.appearance-select select').first().selectOption('dark')
  for (const section of ['profile', 'model', 'routines', 'tools']) {
    await selectSettings(page, section)
    await auditWidths(page, `10-configuracoes-${section}-escuro`)
    await page.setViewportSize({ width: 1280, height: 900 })
  }
  await page.getByRole('button', { name: 'Revogar uso', exact: true }).first().click()
  await page.getByRole('button', { name: 'Confirmar alteração', exact: true }).hover()
  await auditWidths(page, '10-configuracoes-confirmacao-escura')
  await page.locator('.tool-confirmation').getByRole('button', { name: 'Cancelar', exact: true }).click()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.locator('.appearance-select select').first().selectOption('light')
  // Navegação cabe também na janela baixa, sem cortar as últimas seções ou rolar a página.
  await page.setViewportSize({ width: 768, height: 500 })
  await selectSettings(page, 'usage')
  await audit(page, '11-configuracoes-768')
  await page.setViewportSize({ width: 1920, height: 900 })
  await selectSettings(page, 'profile')
  await audit(page, '11-configuracoes-1920')
  // Atualização de A em voo não pode repor sua lista depois da troca para B (#210).
  await selectSettings(page, 'model')
  await page.getByRole('combobox', { name: 'Provedor', exact: true }).selectOption('custom_ollama')
  await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${localPortA}`)
  const local = page.locator('.local-model')
  await local.getByText('modelo-local-a:latest', { exact: true }).waitFor()
  localBackends[0].state.holdTags = true
  await local.getByRole('button', { name: 'Verificar de novo', exact: true }).click()
  await waitFor(() => localBackends[0].state.waiting.length === 1, 'consulta antiga em voo')
  await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${localPortB}`)
  await local.getByText('modelo-local-b:latest', { exact: true }).waitFor()
  localBackends[0].release()
  await page.waitForTimeout(300)
  if (await local.getByText('modelo-local-a:latest', { exact: true }).count()) throw new Error('Resposta antiga substituiu os modelos do endpoint atual.')
  await local.getByRole('button', { name: 'Baixar', exact: true }).first().click()
  const confirmation = page.getByRole('dialog')
  await confirmation.getByText(`http://127.0.0.1:${localPortB}`, { exact: false }).waitFor()
  await confirmation.getByRole('button', { name: 'Cancelar', exact: true }).click()
  if (localBackends.some(({ state }) => state.writes)) throw new Error('Troca ou confirmação cancelada iniciou uma operação no Ollama.')
  await audit(page, '12-modelo-local-endpoint-atual')
  await page.getByRole('button', { name: 'Voltar à conversa' }).click()
  if (model.requests - before !== 2) throw new Error(`Modo agente: esperava 2 gerações, houve ${model.requests - before}.`)
  // Fechar antes de reduzir a janela: a folha móvel do computador cobre o chat.
  const closePanel = page.getByRole('button', { name: 'Fechar painel', exact: true })
  if (await closePanel.isVisible()) await closePanel.click()
  // Limpeza só arquiva: cancelar preserva a conversa; confirmar cria contexto vazio sem gerar.
  const clearChat = page.getByRole('button', { name: 'Limpar conversa', exact: true })
  const clearDialog = page.getByRole('dialog')
  const cancelClear = clearDialog.getByRole('button', { name: 'Cancelar', exact: true })
  const draft = 'Rascunho antes de cancelar a limpeza'
  await page.locator('#chat-input').fill(draft)
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await waitFor(() => clearChat.isEnabled(), `Limpar conversa disponível a ${width} px`, 10_000)
    if (await clearChat.getAttribute('aria-haspopup') !== 'dialog') throw new Error('Limpar conversa não anuncia o diálogo.')
    await clearChat.press('Enter')
    await cancelClear.waitFor()
    await waitFor(() => cancelClear.evaluate((element) => element === document.activeElement), `foco inicial em Cancelar a ${width} px`, 5_000)
    await page.keyboard.press('Escape')
    await clearDialog.waitFor({ state: 'hidden' })
    await waitFor(() => clearChat.evaluate((element) => element === document.activeElement), `foco em Limpar conversa após Escape a ${width} px`, 5_000)
    await page.keyboard.press('Space')
    await cancelClear.waitFor()
    await page.keyboard.press('Enter')
    await clearDialog.waitFor({ state: 'hidden' })
    await waitFor(() => clearChat.evaluate((element) => element === document.activeElement), `foco em Limpar conversa após Cancelar a ${width} px`, 5_000)
    await page.getByRole('log').getByText(marker).last().waitFor()
    if (await page.locator('#chat-input').inputValue() !== draft) throw new Error('Cancelar a limpeza descartou o rascunho.')
  }
  await page.keyboard.press('Enter')
  await page.getByRole('dialog').getByRole('button', { name: 'Limpar conversa', exact: true }).click()
  await page.getByRole('heading', { name: 'Como posso ajudar?' }).waitFor()
  await auditWidths(page, '13-conversa-limpa')
  if (model.requests !== before + 2) throw new Error('Limpar a conversa iniciou uma geração.')
  // Recusa do modelo precisa explicar a causa no turno, preservando o aviso de resultado incerto.
  model.failNext = true
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.locator('#chat-input').fill('Teste de diagnóstico do modelo')
  await page.locator('#chat-input').press('Enter')
  await page.getByText('O provedor recusou o formato de uma ferramenta.', { exact: false }).waitFor({ timeout: 60_000 })
  await page.getByText('Invalid schema: required must list every property.', { exact: false }).waitFor()
  if ((await page.getByRole('log').textContent()).includes('chave-e2e') || (await page.getByRole('log').textContent()).includes('private.invalid')) throw new Error('O diagnóstico expôs dados privados do provedor.')
  await page.getByText('Detalhes do erro', { exact: true }).click()
  await page.getByText('tools.function.parameters', { exact: true }).waitFor()
  await auditWidths(page, '14-erro-modelo-explicado')
  // Resposta HTTP 200 inválida: causa estrutural no turno, Atividade e Tarefas, sem retry.
  await page.setViewportSize({ width: 1280, height: 900 })
  model.invalidArguments = true
  await page.locator('#chat-input').fill('Teste de argumentos inválidos')
  await page.locator('#chat-input').press('Enter')
  const reason = 'Os argumentos da ferramenta não formam um objeto JSON válido.'
  await page.getByRole('log').getByText(reason, { exact: true }).waitFor({ timeout: 60_000 })
  if ((await page.getByRole('log').textContent()).includes('INVALID-PRIVATE-ARGUMENTS')) throw new Error('O diagnóstico publicou argumentos inválidos privados.')
  const turnDetails = page.getByRole('log').getByRole('button', { name: 'Ver detalhes', exact: true }).last()
  await checkTaskDetailsKeyboard(page, turnDetails, reason, 'Ver detalhes na conversa')
  await page.getByRole('button', { name: 'Atividade', exact: true }).click()
  const activityRow = page.getByRole('tabpanel').getByRole('button', { name: /Teste de argumentos inválidos/ })
  await activityRow.locator('.task-failure-summary').filter({ hasText: reason }).waitFor()
  await checkTaskDetailsKeyboard(page, activityRow, reason, 'tarefa em Atividade no desktop')
  await auditWidths(page, '15-causa-na-atividade')
  await page.getByRole('dialog', { name: 'Painel lateral', exact: true }).waitFor()
  await checkTaskDetailsKeyboard(page, activityRow, reason, 'tarefa na gaveta móvel de Atividade')
  const closeActivity = page.getByRole('button', { name: 'Fechar painel', exact: true })
  if (await closeActivity.isVisible()) await closeActivity.click()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Configurações da abelha' }).first().click()
  await selectSettings(page, 'tasks')
  const badTask = page.locator('.task-item').filter({ hasText: 'Teste de argumentos inválidos' })
  await badTask.getByText(reason, { exact: false }).waitFor()
  await badTask.click()
  await page.locator('.task-detail').getByText(reason, { exact: true }).waitFor()
  // Consulta canônica real: não gera nem tenta reconciliar a chamada inválida.
  const callsBeforeDiagnosis = model.requests
  for (const width of [390, 1280]) {
    await page.setViewportSize({ width, height: 900 })
    const runs = page.locator('.recovery-runs')
    await runs.getByRole('button', { name: 'Consultar primeira página', exact: true }).focus()
    await page.keyboard.press('Enter')
    await runs.locator('li').first().waitFor()
    if (await runs.locator('li').count() !== 1) throw new Error('Diagnóstico não encontrou a única execução da tarefa.')
    await runs.locator('summary').first().focus()
    await page.keyboard.press('Enter')
    if (!await runs.getByText('ID da execução', { exact: true }).isVisible()) throw new Error('ID da execução inacessível por teclado.')
    if (!await runs.getByRole('button', { name: 'Próxima página', exact: true }).isDisabled()) throw new Error('Diagnóstico criou página de execuções sem cursor.')
    if ((await runs.textContent()).includes('INVALID-PRIVATE-ARGUMENTS')) throw new Error('Execução expôs conteúdo privado.')
    const canonicalRun = await runs.locator('code').first().textContent()
    await runs.locator('summary').first().press('Enter')
    const records = page.locator('.recovery-model-calls')
    const firstPage = records.getByRole('button', { name: 'Consultar primeira página', exact: true })
    await firstPage.focus()
    await page.keyboard.press('Enter')
    await records.locator('li').first().waitFor()
    if (await records.locator('li').count() !== 1) throw new Error('Diagnóstico não encontrou a única chamada da tarefa.')
    await records.locator('summary').first().focus()
    await page.keyboard.press('Enter')
    if (!await records.getByRole('button', { name: 'Próxima página', exact: true }).isDisabled()) throw new Error('Diagnóstico criou página adicional sem cursor.')
    if (!await records.getByText('ID da chamada', { exact: true }).isVisible()) throw new Error('IDs do diagnóstico inacessíveis por teclado.')
    if ((await records.textContent()).includes('INVALID-PRIVATE-ARGUMENTS')) throw new Error('Diagnóstico expôs conteúdo privado.')
    if (await records.locator('code').nth(1).textContent() !== canonicalRun) throw new Error('Chamada não está vinculada à execução consultada.')
    // Fecha o detalhe para exercitar o mesmo caminho na próxima largura.
    await records.locator('summary').first().press('Enter')
  }
  if (model.requests !== callsBeforeDiagnosis) throw new Error('Consulta de diagnóstico gerou nova chamada.')
  await auditWidths(page, '16-causa-na-tarefa')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.locator('.appearance-select select').first().selectOption('dark')
  await auditWidths(page, '16-causa-na-tarefa-escuro')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.locator('.appearance-select select').first().selectOption('light')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Voltar à conversa' }).click()
  // Falha real do caminho web: endereço privado é barrado antes da rede, com código no journal.
  model.failedTool = true
  await page.locator('#chat-input').fill('Teste de falha da ferramenta web')
  await waitFor(() => page.getByRole('button', { name: 'Enviar', exact: true }).isEnabled(), 'editor pronto após configurações')
  await page.locator('#chat-input').press('Enter')
  await waitFor(() => model.requests === expectedModelRequests, 'tarefa com ferramenta recusada', 90_000)
  await page.getByRole('button', { name: 'Configurações da abelha' }).first().click()
  await selectSettings(page, 'tasks')
  await page.locator('.task-item').filter({ hasText: 'Teste de falha da ferramenta web' }).click()
  await page.locator('.task-detail').getByText('Ações de ferramentas registradas', { exact: true }).click()
  await page.locator('.task-detail').getByText('web_private_address_blocked', { exact: true }).waitFor()
  await page.locator('.task-detail').getByText('endereço privado bloqueado', { exact: false }).waitFor()
  await auditWidths(page, '17-falha-da-ferramenta')
  if (responsesMode) {
    // A configuração de visão é explícita; o aviso deve indicar onde alterá-la.
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.getByRole('button', { name: 'Nova abelha' }).click()
    await page.getByLabel('Provedor').selectOption('custom')
    await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${modelPort}/v1`)
    await page.getByLabel('Credencial da sua conta Bees').selectOption({ label: 'Chave salva 1' })
    await page.getByLabel(/^Digitar outro modelo/).check()
    await page.getByLabel(/^Identificador do modelo|^Modelo/).last().fill('gpt-5.5')
    await page.getByLabel(/declara suporte a chamadas de ferramentas/).evaluate((element) => { if (!element.checked) element.click() })
    await page.getByLabel('Nome da abelha').fill('Visão desativada')
    await page.getByRole('textbox', { name: 'Propósito' }).fill('Conferir orientação de imagens.')
    await page.getByRole('button', { name: 'Criar abelha' }).click()
    await page.getByRole('heading', { name: 'Visão desativada', exact: true }).waitFor()
    // Os pacotes já existem; conceder ferramentas explicitamente à abelha desta fixture.
    await page.evaluate(async () => {
      const auth = await (await fetch('/api/v1/auth/status')).json()
      const data = await (await fetch('/api/v1/onboarding')).json()
      const agent = data.agents.find((item) => item.name === 'Visão desativada')
      const response = await fetch(`/api/v1/agents/${agent.id}/agent/recommended-tools`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Bees-CSRF': auth.csrf_token }, body: JSON.stringify({ client_request_id: crypto.randomUUID() }) })
      if (!response.ok) throw new Error('Não foi possível conceder ferramentas à fixture de visão.')
    })
    await page.reload()
    if (await page.getByRole('button', { name: 'Voltar à conversa' }).isVisible()) await page.getByRole('button', { name: 'Voltar à conversa' }).click()
    await waitFor(() => page.getByRole('button', { name: /^Anexar arquivo/ }).isEnabled(), 'anexos prontos')
    await page.locator('#chat-input').evaluate((input, data) => {
      const transfer = new DataTransfer()
      transfer.items.add(new File([Uint8Array.from(atob(data.split(',')[1]), (char) => char.charCodeAt(0))], 'aviso.png', { type: 'image/png' }))
      input.dispatchEvent(new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }))
    }, model.expectedImages[0])
    await page.getByText('A visão está desativada na configuração desta abelha.', { exact: false }).waitFor()
    await page.getByText('Configurações da abelha → Modelo → Opções avançadas', { exact: false }).waitFor()
    await auditWidths(page, '18-orientacao-de-visao')
  }
  if (responsesMode && (model.responsesRequests !== expectedModelRequests || model.responsesContinuations !== 2)) throw new Error(`Responses: ${model.responsesRequests} chamadas, ${model.responsesContinuations} continuações.`)
  const serious = violations.filter((item) => ['serious', 'critical'].includes(item.impact))
  writeFileSync(join(out, 'e2e.json'), JSON.stringify({ model_requests: model.requests, responses_requests: model.responsesRequests, responses_continuations: model.responsesContinuations, violations }, null, 2))
  console.log(JSON.stringify({ model_requests: model.requests, violations: violations.length, serious: serious.length }))
  if (model.requests !== expectedModelRequests) throw new Error(`Esperava ${expectedModelRequests} chamadas, houve ${model.requests}.`)
  if (serious.length) { console.error(JSON.stringify(serious, null, 2)); process.exitCode = 1 }
} catch (error) {
  console.error(error)
  await browser?.contexts()[0]?.pages().at(-1)?.screenshot({ path: join(out, 'falha.png'), fullPage: true }).catch(() => undefined)
  console.error('gerações do modelo:', model.requests)
  const debug = await browser?.contexts()[0]?.pages().at(-1)?.evaluate(async () => {
    const data = await (await fetch('/api/v1/onboarding')).json()
    const agent = data.agents.at(-1)
    const delegations = await (await fetch(`/api/v1/agents/${agent.id}/delegations?conversation_id=${agent.conversation_id}&offset=0&limit=100`)).json()
    const messages = await (await fetch(`/api/v1/agents/${agent.id}/messages?conversation_id=${agent.conversation_id}`)).json()
    return JSON.stringify({ delegations, messages: messages.messages?.map((m) => ({ id: m.id, role: m.role })) }).slice(0, 3000)
  }).catch((failure) => String(failure))
  console.error('depuração:', debug)
  process.exitCode = 1
} finally {
  await browser?.close()
  for (const child of children) child.kill()
  modelServer.close()
  for (const backend of localBackends) { backend.release(); backend.server.close() }
  try { rmSync(data, { recursive: true, force: true }) } catch { /* processo ainda fechando */ }
}
