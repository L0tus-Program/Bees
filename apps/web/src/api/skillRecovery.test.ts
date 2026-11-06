// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { checkedSkillIntent, enableIntent, importIntent, skillIntent } from './skills'
import { clearSkillMutation, preserveSkillMutation, restoreSkillMutation } from './skillRecovery'
import { agentId, definition, skill } from '../features/skills/fixtures'

const key = `bees.skill-library.pending.v1.${agentId}`
const other = '33333333-3333-4333-8333-333333333333'
beforeEach(() => sessionStorage.clear())
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const commands = () => [skillIntent(agentId, definition), skillIntent(agentId, definition, skill), importIntent(agentId, { format: 'bees.skill/v1', scope: 'agent', definition }), enableIntent(skill), enableIntent({ ...skill, enabled_version: 1 })]

describe('closed library command recovery', () => {
  it.each([0, 1, 2, 3, 4])('round-trips operation %s without changing identity or nested content', (index) => {
    const intent = commands()[index]
    const snapshot = preserveSkillMutation(intent)
    expect(snapshot).toEqual(JSON.parse(JSON.stringify(intent)))
    expect(restoreSkillMutation(agentId)).toEqual({ intent: snapshot, corrupt: false })
    expect(restoreSkillMutation(other)).toEqual({ intent: null, corrupt: false })
    clearSkillMutation(agentId)
    expect(restoreSkillMutation(agentId)).toEqual({ intent: null, corrupt: false })
  })
  it('copies the definition before storing and returning the command', () => {
    const source = structuredClone(definition)
    const intent = skillIntent(agentId, source)
    const checked = checkedSkillIntent(intent, agentId)
    const snapshot = preserveSkillMutation(intent)
    source.steps[0].instruction = 'Later edit'
    expect(snapshot.body).toEqual(checked.body)
    expect(restoreSkillMutation(agentId).intent).toEqual(checked)
  })
  it.each(['', '{', 'null', '[]', '{}'])('blocks unreadable stored data %s without removing it', (raw) => {
    sessionStorage.setItem(key, raw)
    expect(restoreSkillMutation(agentId)).toEqual({ intent: null, corrupt: true })
    expect(sessionStorage.getItem(key)).toBe(raw)
  })
  it.each(['agent', 'path', 'method', 'extra', 'body', 'process'])('rejects stored command with incompatible %s', (field) => {
    const intent = JSON.parse(JSON.stringify(skillIntent(agentId, definition)))
    if (field === 'agent') intent.agentId = other
    if (field === 'path') intent.path = `/agents/${other}/skills`
    if (field === 'method') intent.method = 'PUT'
    if (field === 'extra') intent.extra = true
    if (field === 'body') intent.body.enabled = true
    if (field === 'process') intent.path += '/from-process'
    sessionStorage.setItem(key, JSON.stringify(intent))
    expect(restoreSkillMutation(agentId)).toEqual({ intent: null, corrupt: true })
    expect(() => preserveSkillMutation(intent)).toThrow()
  })
  it('blocks inaccessible storage and exposes no raw storage diagnostic', () => {
    vi.stubGlobal('sessionStorage', { getItem: () => { throw new Error('Private browser diagnostic') } })
    expect(restoreSkillMutation(agentId)).toEqual({ intent: null, corrupt: true })
  })
})
