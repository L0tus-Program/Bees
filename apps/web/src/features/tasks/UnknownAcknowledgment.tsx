import { useTranslation } from 'react-i18next'

export function UnknownAcknowledgment({ kind, checked, onChange }: { kind: 'model' | 'tool' | 'mixed'; checked: boolean; onChange: (checked: boolean) => void }) {
  const { t } = useTranslation('tasks')
  return <label className="checkbox-field"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><span>{t(kind === 'tool' ? 'toolAcknowledgeUnknown' : kind === 'mixed' ? 'mixedAcknowledgeUnknown' : 'acknowledgeUnknown')}</span></label>
}
