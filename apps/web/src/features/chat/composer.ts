export interface ComposerState { draft: string; allowDelegation: boolean; failed: boolean }
export type ComposerAction = { type: 'draft'; value: string } | { type: 'consent'; value: boolean } | { type: 'failure' } | { type: 'success' }
export const emptyComposer: ComposerState = { draft: '', allowDelegation: false, failed: false }

// Keep the exact failed request available for human reconciliation. An edited
// message is a new request and cannot inherit the previous turn's consent.
export function composerReducer(state: ComposerState, action: ComposerAction): ComposerState {
  switch (action.type) {
    case 'draft': return { ...state, draft: action.value, ...(state.failed && action.value !== state.draft ? { allowDelegation: false, failed: false } : {}) }
    case 'consent': return { ...state, allowDelegation: action.value }
    case 'failure': return { ...state, failed: true }
    case 'success': return emptyComposer
  }
}
