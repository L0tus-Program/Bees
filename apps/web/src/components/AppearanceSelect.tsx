import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { appearanceKey, appearanceQuery, applyAppearance, isAppearance, readAppearance } from './appearance'
import type { Appearance } from './appearance'

export function AppearanceSelect() {
  const { t } = useTranslation('product')
  const [value, setValue] = useState<Appearance>(readAppearance)
  const [unsaved, setUnsaved] = useState(false)
  useEffect(() => {
    applyAppearance(value)
    const media = window.matchMedia?.(appearanceQuery)
    const changed = () => { if (value === 'system') applyAppearance(value) }
    media?.addEventListener('change', changed)
    return () => media?.removeEventListener('change', changed)
  }, [value])
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      try { if (event.storageArea !== localStorage) return } catch { return }
      if (event.key !== appearanceKey && event.key !== null) return
      setValue(isAppearance(event.newValue) ? event.newValue : 'system'); setUnsaved(false)
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [])
  function choose(next: string) {
    if (!isAppearance(next)) return
    setValue(next)
    try { localStorage.setItem(appearanceKey, next); setUnsaved(false) } catch { setUnsaved(true) }
  }
  return <div className="appearance-control"><label className="appearance-select"><span className="sr-only">{t('appearance')}</span><select value={value} aria-describedby="appearance-scope" onChange={(event) => choose(event.target.value)}><option value="system">{t('appearanceSystem')}</option><option value="light">{t('appearanceLight')}</option><option value="dark">{t('appearanceDark')}</option></select></label><span id="appearance-scope" className="sr-only">{t('appearanceScope')}</span>{unsaved && <span role="status" className="appearance-unsaved">{t('appearanceUnsaved')}</span>}</div>
}
