import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { configurationSignature, testModel } from '../../api/onboarding'
import type { Diagnostic, ModelConfig } from '../../api/onboarding'
import { canReuseSecret, connectionConfig } from './connection'

export function useModelConnection(initial?: ModelConfig | null, agentId?: string) {
  const [kind, setKind] = useState<ModelConfig['kind']>(initial?.kind ?? 'openai_compatible')
  const [endpoint, setEndpoint] = useState(initial?.endpoint ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [tools, setTools] = useState(initial?.capabilities.tool_calls ?? false)
  const [apiKey, setApiKey] = useState('')
  const [reuse, setReuse] = useState(false)
  const [receipt, setReceipt] = useState<{ signature: string; diagnostic: Diagnostic } | null>(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const controller = useRef<AbortController | null>(null)
  useEffect(() => () => controller.current?.abort(), [])
  useEffect(() => {
    if (!receipt) return
    const timeout = setTimeout(() => setReceipt(null), 300000)
    return () => clearTimeout(timeout)
  }, [receipt])
  const config = connectionConfig(initial, { kind, endpoint, model, tools, reuse })
  const signature = configurationSignature(config, apiKey)

  function invalidate() { setReceipt(null); setError(null) }
  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (testing) return
    invalidate(); setTesting(true)
    controller.current = new AbortController()
    try {
      const diagnostic = await testModel(config, apiKey, controller.current.signal, agentId)
      if (!controller.current.signal.aborted) setReceipt({ signature, diagnostic })
    } catch (failure) { if (!controller.current.signal.aborted) setError(asApiError(failure)) }
    finally { setTesting(false) }
  }

  return {
    kind, endpoint, model, tools, apiKey, reuse, config, error, testing, check,
    canReuse: canReuseSecret(initial, config), validated: receipt?.signature === signature ? receipt.diagnostic : null,
    invalidate,
    clear: () => { setApiKey(''); setReuse(false); setReceipt(null) },
    changeKind: (value: ModelConfig['kind']) => { invalidate(); setKind(value); setEndpoint(value === 'ollama' ? 'http://127.0.0.1:11434' : ''); setModel(''); setApiKey(''); setReuse(false); setTools(false) },
    changeEndpoint: (value: string) => { invalidate(); setEndpoint(value); setApiKey(''); setReuse(false) },
    changeModel: (value: string) => { invalidate(); setModel(value) },
    changeTools: (value: boolean) => { invalidate(); setTools(value) },
    changeKey: (value: string) => { invalidate(); setApiKey(value); setReuse(false) },
    changeReuse: (value: boolean) => { invalidate(); setReuse(value); setApiKey('') },
  }
}
