import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BUILTIN_TEMPLATES, parseTemplate } from './templates'
import type { BeeTemplate, BuiltinTemplate } from './templates'

const TOOL_TEMPLATES = new Set<BuiltinTemplate>(['researcher', 'news', 'documents', 'organizer'])

export function TemplatePicker({ onApply, disabled }: { onApply: (template: BeeTemplate) => void; disabled?: boolean }) {
  const { t } = useTranslation('templates')
  const [chosen, setChosen] = useState<BuiltinTemplate | ''>('')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  function choose(value: BuiltinTemplate | '') {
    setChosen(value); setNotice(null)
    if (!value) return
    onApply({ name: t(`templates.${value}.name`), purpose: t(`templates.${value}.purpose`), instructions: t(`templates.${value}.instructions`) })
  }
  async function importFile(file: File | undefined) {
    if (!file) return
    const template = file.size <= 64_000 ? parseTemplate(await file.text()) : null
    if (!template) { setNotice({ kind: 'error', text: t('invalid') }); return }
    setChosen(''); onApply(template); setNotice({ kind: 'ok', text: t('imported', { name: template.name }) })
  }
  return (
    <div className="template-picker">
      <label className="field" htmlFor="bee-template"><span>{t('title')}</span>
        <select id="bee-template" value={chosen} disabled={disabled} onChange={(event) => choose(event.target.value as BuiltinTemplate | '')}>
          <option value="">{t('none')}</option>
          {BUILTIN_TEMPLATES.map((item) => <option key={item} value={item}>{t(`templates.${item}.label`)}</option>)}
        </select>
        <small>{t('help')}</small>
      </label>
      {chosen && TOOL_TEMPLATES.has(chosen) && <p className="quiet-note">{t('toolsHint')}</p>}
      <label className="text-button file-import"><span>{t('import')}</span><input type="file" accept="application/json,.json" className="sr-only" disabled={disabled} onChange={(event) => { void importFile(event.target.files?.[0]); event.target.value = '' }} /></label>
      {notice && <p className={notice.kind === 'ok' ? 'form-success' : 'transfer-notice'} role={notice.kind === 'ok' ? 'status' : 'alert'}>{notice.text}</p>}
    </div>
  )
}
