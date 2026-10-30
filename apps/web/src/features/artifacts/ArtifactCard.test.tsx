import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { ArtifactRecord } from './api'
import { ArtifactCard } from './ArtifactCard'
import { TaskArtifacts } from './TaskArtifacts'
import { artifactResources } from '../../locales/artifacts'
import { artifact, agentId, taskId } from './fixtures'

async function render(content: React.ReactNode, language: 'pt-BR' | 'en' | 'es' = 'pt-BR') {
  const i18n = createInstance()
  await i18n.init({ lng: language, resources: { [language]: { artifacts: artifactResources[language] } }, interpolation: { escapeValue: false } })
  return renderToStaticMarkup(<I18nextProvider i18n={i18n}>{content}</I18nextProvider>)
}
describe('artifact cards', () => {
  it.each(['pt-BR', 'en', 'es'] as const)('presents the file, exact version and provenance in %s', async (language) => {
    const html = await render(<ArtifactCard artifact={artifact} busy={false} blocked={false} onDownload={() => {}} />, language)
    expect(html).toContain(artifact.name); expect(html).toContain(artifact.media_type); expect(html).toContain(artifact.sha256)
    expect(html).toContain(artifactResources[language].provenance); expect(html).toContain(artifact.run_id); expect(html).toContain(artifact.task_id)
    expect(html).toContain('aria-label='); expect(html).toContain('<details'); expect(html).not.toContain('<iframe'); expect(html).not.toContain('<a ')
  })
  it.each(['draft', 'failed', 'deleted'] as ArtifactRecord['status'][])('does not offer downloading state %s', async (status) => {
    const html = await render(<ArtifactCard artifact={{ ...artifact, status }} busy={false} blocked={false} onDownload={() => {}} />)
    expect(html).not.toContain('<button'); expect(html).toContain(artifactResources['pt-BR'].unavailable)
  })
  it('blocks downloads while loading and never implies the file was saved', async () => {
    const html = await render(<ArtifactCard artifact={artifact} busy blocked onDownload={() => {}} />)
    expect(html).toContain('disabled=""'); expect(html).toContain(artifactResources['pt-BR'].downloading)
    expect(html).not.toContain(artifactResources['pt-BR'].sent)
  })
  it('escapes filenames rather than rendering content', async () => {
    const html = await render(<ArtifactCard artifact={{ ...artifact, name: '<script>alert(1)</script>.txt' }} busy={false} blocked={false} onDownload={() => {}} />)
    expect(html).toContain('&lt;script&gt;'); expect(html).not.toContain('<script>')
  })
  it.each(['pt-BR', 'en', 'es'] as const)('starts with an explicit loading state and scope in %s', async (language) => {
    const html = await render(<TaskArtifacts agentId={agentId} taskId={taskId} />, language)
    expect(html).toContain(artifactResources[language].loading); expect(html).toContain(artifactResources[language].scope)
    expect(html).toContain('aria-busy="true"'); expect(html).toContain(artifactResources[language].refresh)
    expect(html).not.toContain(artifactResources[language].empty)
  })
})
