import type { i18n } from 'i18next'

export function failureMessage(i18n: i18n, code: string, source: 'model' | 'tool' = 'model', issue?: string) {
  if (issue && i18n.exists(`responseIssue.${issue}`, { ns: 'workspace' })) return i18n.t(`responseIssue.${issue}`, { ns: 'workspace' })
  if (source === 'tool' && ['outcome_unknown', 'tool_outcome_unknown', 'mcp_outcome_unknown', 'executor_result_unknown', 'tool_result_unknown', 'tool_interrupted_unknown'].includes(code)) return i18n.t('toolOutcomeUnknown', { ns: 'workspace' })
  const toolCode = source === 'tool' && code.startsWith('web_') ? code.slice(4) : code
  if (source === 'tool' && i18n.exists(`stepCode.${toolCode}`, { ns: 'agent' })) return i18n.t(`stepCode.${toolCode}`, { ns: 'agent' })
  if (code === 'invalid_response') return i18n.t(source === 'tool' ? 'failureToolResponse' : 'failureModelResponse', { ns: 'workspace' })
  if (i18n.exists(`errors.${code}`, { ns: 'product' })) return i18n.t(`errors.${code}`, { ns: 'product' })
  return i18n.t(code === 'outcome_unknown' ? 'failureUnknown' : 'failureOther', { ns: 'workspace' })
}
