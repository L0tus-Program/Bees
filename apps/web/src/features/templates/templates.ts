// Modelos de abelha: perfil inicial (nome, propósito, instruções) compartilhável como JSON.
// Um modelo nunca carrega credenciais, memórias, conversas ou permissões.

export const TEMPLATE_FORMAT = 'bees.bee-template/v1'
export interface BeeTemplate { name: string; purpose: string; instructions: string }
export const BUILTIN_TEMPLATES = ['assistant', 'researcher', 'news', 'writer', 'documents', 'organizer'] as const
export type BuiltinTemplate = typeof BUILTIN_TEMPLATES[number]

const text = (value: unknown, max: number, required = false) =>
  typeof value === 'string' && value.length <= max && !value.includes('\u0000') && (!required || value.trim().length > 0)

export function parseTemplate(raw: string): BeeTemplate | null {
  if (raw.length > 64_000) return null
  let value: unknown
  try { value = JSON.parse(raw) } catch { return null }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const record = value as Record<string, unknown>
  if (record.format !== TEMPLATE_FORMAT || !text(record.name, 100, true) || !text(record.purpose, 2000, true) || !text(record.instructions ?? '', 10000)) return null
  return { name: (record.name as string).trim(), purpose: (record.purpose as string).trim(), instructions: ((record.instructions as string | undefined) ?? '').trim() }
}

export function serializeTemplate(template: BeeTemplate) {
  return JSON.stringify({ format: TEMPLATE_FORMAT, name: template.name, purpose: template.purpose, instructions: template.instructions }, null, 2)
}

export function downloadTemplate(template: BeeTemplate) {
  const blob = new Blob([serializeTemplate(template)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${template.name.toLowerCase().replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'abelha'}.bee.json`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
