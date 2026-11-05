import type { Skill, SkillDefinition } from '../../api/skills'

export const agentId = '11111111-1111-4111-8111-111111111111'
export const definition: SkillDefinition = {
  name: 'Comparar fontes', purpose: 'Pesquisar e conferir referências', inputs: [{ name: 'tema', description: 'Tema escolhido', required: true }],
  steps: [{ title: 'Conferir', instruction: 'Registrar fonte e data' }], resources: { environment: 'own', tools: ['browser.read'] }, output: 'Relatório', completion: 'Fontes conferidas',
}
export const skill: Skill = { id: '22222222-2222-4222-8222-222222222222', agent_id: agentId, revision: 1, current_version: 1, enabled_version: null, created_at: '2026-10-16T12:00:00Z', updated_at: '2026-10-16T12:00:00Z', current: { skill_id: '22222222-2222-4222-8222-222222222222', version: 1, definition, content_hash: 'b747a86abf695e1872dc6cd8722ee686e942685632e0437592c086a6732ca472', origin: 'human', created_at: '2026-10-16T12:00:00Z' } }
