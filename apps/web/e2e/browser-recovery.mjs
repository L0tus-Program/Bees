// Recuperação Linux: liberar um perfil não pode encerrar outro Chromium de nome semelhante.
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = resolve(web, '../..')
const out = resolve(process.argv[2] ?? join(web, 'e2e-results'))
mkdirSync(out, { recursive: true })
const report = join(out, 'browser-recovery.json')
if (process.platform !== 'linux') {
  writeFileSync(report, JSON.stringify({ status: 'skipped', reason: 'linux_proc_required' }) + '\n')
  process.exit(0)
}

const data = mkdtempSync(join(tmpdir(), 'bees-browser-recovery-'))
const profile = join(data, 'perfil com espaços')
const sibling = `${profile}-outra`
const python = process.env.BEES_PYTHON ?? join(root, '.venv/bin/python')
const contexts = []
let phase = 'start_profiles'
try {
  const own = await chromium.launchPersistentContext(profile, { headless: true })
  contexts.push(own)
  const other = await chromium.launchPersistentContext(sibling, { headless: true })
  contexts.push(other)
  const otherPage = other.pages()[0]
  await otherPage.setContent('<title>Perfil independente</title><p>SESSAO-PRESERVADA</p>')
  phase = 'release_exact_profile'
  const closed = own.waitForEvent('close', { timeout: 10000 }).then(() => true, () => false)
  execFileSync(python, ['-c', 'import sys; from pathlib import Path; from bees_executor.browser import release_profile; release_profile(Path(sys.argv[1]))', profile], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: [join(root, 'apps/executor/src'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
    stdio: 'pipe',
    timeout: 10000,
  })
  if (!await closed) throw new Error('exact_profile_not_closed')
  phase = 'check_sibling'
  if (await otherPage.textContent('body') !== 'SESSAO-PRESERVADA') throw new Error('sibling_interrupted')
  if (!existsSync(join(profile, 'Default'))) throw new Error('profile_data_missing')
  phase = 'reopen_profile'
  const reopened = await chromium.launchPersistentContext(profile, { headless: true })
  contexts.push(reopened)
  await reopened.pages()[0].setContent('<title>Perfil recuperado</title><p>RECUPERADO</p>')
  if (await reopened.pages()[0].textContent('body') !== 'RECUPERADO') throw new Error('profile_not_recovered')
  const result = { status: 'verified', exact_profile_closed: true, sibling_preserved: true, profile_reopened: true }
  writeFileSync(report, JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result))
} catch {
  writeFileSync(report, JSON.stringify({ status: 'error', phase }) + '\n')
  console.error(`browser_profile_recovery_failed:${phase}`)
  process.exitCode = 1
} finally {
  for (const context of contexts.reverse()) await context.close().catch(() => undefined)
  rmSync(data, { recursive: true, force: true })
}
