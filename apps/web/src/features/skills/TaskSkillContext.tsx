import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { getSkillContext } from '../../api/skillTasks'
import type { TaskRecord } from '../../api/tasks'
import { ErrorNotice } from '../../components/Feedback'
import { SkillDescription } from './SkillContent'

export function TaskSkillContext({ task }: { task: TaskRecord }) {
  const { t } = useTranslation('skills')
  const [context, setContext] = useState<Awaited<ReturnType<typeof getSkillContext>> | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const operation = useRef<AbortController | null>(null)
  useEffect(() => () => operation.current?.abort(), [])
  if (!task.skill_execution) return null
  async function read() {
    if (!task.skill_execution || busy) return
    setBusy(true); setError(null)
    const controller = new AbortController(); operation.current = controller
    try { const value = await getSkillContext(task.agent_id, task.id, task.skill_execution, controller.signal); if (!controller.signal.aborted) setContext(value) }
    catch (failure) { if (!controller.signal.aborted) setError(asApiError(failure)) }
    finally { if (!controller.signal.aborted) setBusy(false) }
  }
  return <section className="skill-task-context"><p>{t('runFixed', { version: task.skill_execution.version })}</p><button type="button" className="text-button" disabled={busy} onClick={() => { void read() }}>{t('runRead')}</button>{error && <ErrorNotice error={error} />}{context && <><SkillDescription definition={context.definition} /><dl>{Object.entries(context.inputs).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></>}</section>
}
