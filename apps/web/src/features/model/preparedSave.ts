import { prepareModel } from '../../api/onboarding'
import type { ModelConfig } from '../../api/onboarding'

export async function preparedSave<T>(config: ModelConfig, apiKey: string, signal: AbortSignal, save: (token: string) => Promise<T>, agentId?: string): Promise<T> {
  signal.throwIfAborted()
  const receipt = await prepareModel(config, apiKey, signal, agentId)
  // Configuration edits, logout or unmount cancel the whole operation before persistence.
  signal.throwIfAborted()
  return save(receipt.validation_token)
}
