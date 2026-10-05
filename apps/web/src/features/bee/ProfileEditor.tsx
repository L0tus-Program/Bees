import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { saveProfile } from '../../api/bee'
import type { AgentSummary } from '../../api/onboarding'
import { ErrorNotice } from '../../components/Feedback'

export function ProfileEditor({ agent, onSaved, onReload }: { agent: AgentSummary; onSaved: () => void; onReload: () => void }) {
  const { t } = useTranslation('product')
  const { t: bee } = useTranslation('bee')
  const [name, setName] = useState(agent.name)
  const [purpose, setPurpose] = useState(agent.purpose)
  const [instructions, setInstructions] = useState(agent.instructions)
  const [memoryEnabled, setMemoryEnabled] = useState(agent.memory_enabled)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(null); controller.current = new AbortController()
    try {
      await saveProfile(agent.id, agent.revision, { name: name.trim(), purpose: purpose.trim(), instructions: instructions.trim(), memory_enabled: memoryEnabled }, controller.current.signal)
      if (!controller.current.signal.aborted) onSaved()
    } catch (failure) { if (!controller.current.signal.aborted) setError(asApiError(failure)) }
    finally { setBusy(false) }
  }

  return <form className="surface" onSubmit={(event) => { void submit(event) }}>
    <h2>{bee('profileTitle')}</h2><p className="form-introduction">{bee('profileDescription')}</p>
    {error && <><ErrorNotice error={error} /><button type="button" className="text-button" onClick={onReload}>{bee('reloadCurrent')}</button></>}
    <fieldset disabled={busy}>
      <label className="field" htmlFor="edit-bee-name"><span>{t('beeName')}</span><input id="edit-bee-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={200} /></label>
      <label className="field" htmlFor="edit-bee-purpose"><span>{t('purpose')}</span><textarea id="edit-bee-purpose" value={purpose} onChange={(event) => setPurpose(event.target.value)} rows={3} maxLength={4096} /></label>
      <label className="field" htmlFor="edit-bee-instructions"><span>{t('instructions')}</span><textarea id="edit-bee-instructions" value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={5} maxLength={16384} aria-describedby="instructions-distinction" /><small id="instructions-distinction">{bee('instructionsDistinction')}</small></label>
      <label className="checkbox-field"><input type="checkbox" checked={memoryEnabled} onChange={(event) => setMemoryEnabled(event.target.checked)} /><span>{bee('memoryEnabled')}<small>{bee('memoryEnabledHelp')}</small></span></label>
      <button className="button primary" type="submit">{busy ? t('working') : bee('saveProfile')}</button>
    </fieldset>
    <p className="quiet-note">{bee('profileResourceScope')}</p>
  </form>
}
