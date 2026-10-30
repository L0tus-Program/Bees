// Fluxo ponta a ponta com interface compilada, API, worker e modelo simulado por HTTP.
// Primeiro acesso → criar abelha → conversar; axe-core em cada tela (390 e 1280 px,
// pt-BR/en/es). Uso: npm run build && node e2e/flow.mjs [pasta-de-evidências]
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
mkdirSync(out, { recursive: true })

function freePort() {
  return new Promise((done) => { const server = createServer(); server.listen(0, '127.0.0.1', () => { const { port } = server.address(); server.close(() => done(port)) }) })
}

// Modelo OpenAI-compatível: devolve um texto com marcador e conta as gerações.
const model = { requests: 0 }
const modelServer = createServer((request, response) => {
  let body = ''
  request.on('data', (chunk) => { body += chunk })
  request.on('end', () => {
    response.setHeader('content-type', 'application/json')
    if (request.method === 'GET' && request.url.endsWith('/models')) { response.end(JSON.stringify({ data: [{ id: 'modelo-e2e' }] })); return }
    model.requests += 1
    const payload = JSON.parse(body || '{}')
    const messages = payload.messages ?? []
    if (payload.tools?.length && !messages.some((item) => item.role === 'tool')) {
      const call = { id: 'call-1', type: 'function', function: { name: 'publish_file', arguments: JSON.stringify({ name: 'relatorio-e2e', kind: 'markdown', content: '# Relatório\n\nConteúdo de teste.' }) } }
      response.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: null, tool_calls: [call] }, finish_reason: 'tool_calls' }], usage: { prompt_tokens: 3, completion_tokens: 5 } }))
      return
    }
    const last = messages.filter((item) => item.role === 'user').at(-1)?.content ?? ''
    response.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: `${marker}: recebi "${String(last).slice(0, 40)}".` }, finish_reason: 'stop' }], usage: { prompt_tokens: 3, completion_tokens: 5 } }))
  })
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

let browser
const data = mkdtempSync(join(tmpdir(), 'bees-e2e-'))
try {
  const [apiPort, modelPort] = await Promise.all([freePort(), freePort()])
  await new Promise((done) => modelServer.listen(modelPort, '127.0.0.1', done))
  const env = { BEES_DATA_DIR: data, BEES_PORT: String(apiPort), BEES_WEB_DIST: join(web, 'dist') }
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
  await page.getByLabel(/^Digitar outro modelo/).check()
  await page.getByLabel(/^Identificador do modelo|^Modelo/).last().fill('modelo-e2e')
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
  // Modo agente: abelha com ferramentas publica um arquivo, que aparece na resposta para baixar.
  const before = model.requests
  await page.locator('.language-select select').first().selectOption('pt-BR')
  await page.getByRole('button', { name: 'Nova abelha' }).click()
  await page.getByRole('heading', { name: 'Crie sua abelha.' }).waitFor()
  await page.getByLabel('Provedor').selectOption('custom')
  await page.getByLabel(/^Endereço da API/).fill(`http://127.0.0.1:${modelPort}/v1`)
  await page.getByLabel(/^Digitar outro modelo/).check()
  await page.getByLabel(/^Identificador do modelo|^Modelo/).last().fill('modelo-agente')
  await page.getByLabel(/declara suporte a chamadas de ferramentas/).evaluate((element) => { if (!element.checked) element.click() })
  await page.getByLabel('Nome da abelha').fill('Abelha Agente')
  await page.getByRole('textbox', { name: 'Propósito' }).fill('Gerar relatórios de teste.')
  await page.getByRole('button', { name: 'Criar abelha' }).click()
  await page.locator('#chat-input').waitFor({ timeout: 30_000 })
  await page.getByText('Pesquise as novidades', { exact: false }).first().waitFor({ timeout: 30_000 })
  await page.locator('#chat-input').fill('Gere um relatório de teste')
  await page.locator('#chat-input').press('Enter')
  await page.getByRole('link', { name: /relatorio-e2e/ }).waitFor({ timeout: 90_000 })
  await page.getByText(marker).last().waitFor({ timeout: 60_000 })
  await page.getByText('Usou 1 ferramenta').waitFor()
  await auditWidths(page, '6-agente-arquivo')
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByRole('button', { name: 'Atividade', exact: true }).click()
  await page.getByRole('tabpanel').getByText('Gere um relatório de teste').waitFor()
  await audit(page, '7-atividade-lateral')
  await page.getByRole('button', { name: 'Computador', exact: true }).click()
  await page.getByText(/Nenhum runtime de computador|computador desta abelha está desligado/).first().waitFor()
  await audit(page, '7-computador-lateral')
  await page.getByRole('button', { name: 'Configurações da abelha' }).first().click()
  await page.getByRole('button', { name: 'Voltar à conversa' }).waitFor()
  await audit(page, '8-configuracoes')
  await page.getByRole('button', { name: 'Voltar à conversa' }).click()
  if (model.requests - before !== 2) throw new Error(`Modo agente: esperava 2 gerações, houve ${model.requests - before}.`)
  const serious = violations.filter((item) => ['serious', 'critical'].includes(item.impact))
  writeFileSync(join(out, 'e2e.json'), JSON.stringify({ model_requests: model.requests, violations }, null, 2))
  console.log(JSON.stringify({ model_requests: model.requests, violations: violations.length, serious: serious.length }))
  if (model.requests !== 3) throw new Error(`Esperava 3 gerações (1 conversa + 2 do agente), houve ${model.requests}.`)
  if (serious.length) { console.error(JSON.stringify(serious, null, 2)); process.exitCode = 1 }
} catch (error) {
  console.error(error)
  await browser?.contexts()[0]?.pages().at(-1)?.screenshot({ path: join(out, 'falha.png'), fullPage: true }).catch(() => undefined)
  console.error('gerações do modelo:', model.requests)
  const debug = await browser?.contexts()[0]?.pages().at(-1)?.evaluate(async () => {
    const data = await (await fetch('/api/v1/onboarding')).json()
    const agent = data.agents.at(-1)
    const delegations = await (await fetch(`/api/v1/agents/${agent.id}/delegations?conversation_id=${agent.conversation_id}&offset=0&limit=100`)).json()
    const messages = await (await fetch(`/api/v1/agents/${agent.id}/conversations/${agent.conversation_id}/messages`)).json()
    return JSON.stringify({ delegations, messages: messages.messages?.map((m) => ({ id: m.id, role: m.role })) }).slice(0, 3000)
  }).catch((failure) => String(failure))
  console.error('depuração:', debug)
  process.exitCode = 1
} finally {
  await browser?.close()
  for (const child of children) child.kill()
  modelServer.close()
  try { rmSync(data, { recursive: true, force: true }) } catch { /* processo ainda fechando */ }
}
