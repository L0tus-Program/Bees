import { useCallback } from 'react'
import { getTasks } from '../../api/tasks'
import type { TaskRecord, TaskStatus } from '../../api/tasks'
import { useTaskPolling } from '../tasks/useTaskPolling'

export const ACTIVE = new Set<TaskStatus>(['queued', 'running', 'waiting_resource'])
export const needsYou = (task: TaskRecord) => task.status === 'waiting_approval' || task.status === 'paused' || task.unknown_requires_ack

/** Tarefas da abelha observadas fora da conversa (só leitura; a execução é do worker). */
export function useActivity(agentId: string | null) {
  const load = useCallback((signal: AbortSignal) => agentId ? getTasks(agentId, signal) : Promise.resolve({ tasks: [], has_more: false }), [agentId])
  const polling = useTaskPolling(load)
  const tasks = polling.data?.tasks ?? []
  return { ...polling, tasks, active: tasks.filter((task) => ACTIVE.has(task.status)).length, attention: tasks.filter(needsYou).length }
}
