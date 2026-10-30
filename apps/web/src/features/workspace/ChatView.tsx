import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUp, ChevronRight, ListTodo, Loader2, Mic, MicOff, Volume2, VolumeX } from 'lucide-react'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { messages as loadMessages, sendChat } from '../../api/onboarding'
import type { AgentSummary, Message } from '../../api/onboarding'
import { getDelegations } from '../../api/delegations'
import type { Delegation, DelegationCollection } from '../../api/delegations'
import { enableRecommendedTools, getAgentCapabilities, sendAgentMessage } from '../../api/agent'
import type { AgentCapabilities } from '../../api/agent'
import { ErrorNotice } from '../../components/Feedback'
import { useResource } from '../../hooks/useResource'
import { useTaskPolling } from '../tasks/useTaskPolling'
import { commandIdentity } from '../tasks/state'
import { useDictation } from '../voice/useDictation'
import { canSpeak, readVoicePreference, speak, writeVoicePreference } from '../voice/speech'
import { AgentTurn, TurnFiles } from './AgentTurn'
import { Markdown } from '@/components/ui/markdown'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

type Agent = AgentSummary & { conversation_id: string }
const noDelegations: DelegationCollection['delegations'] = []

function mergeDelegations(incoming: DelegationCollection, previous: DelegationCollection) {
  const known = new Map(previous.delegations.map((item) => [item.id, item]))
  return { ...incoming, delegations: incoming.delegations.map((item) => {
    const prior = known.get(item.id)
    return prior && prior.task.revision > item.task.revision ? prior : item
  }) }
}

function Bubble({ message, name }: { message: Message; name: string }) {
  const { t } = useTranslation('workspace')
  if (message.role === 'user') {
    return <div className="flex justify-end"><div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-4 py-2.5 text-[15px] leading-6 whitespace-pre-wrap text-accent-foreground"><span className="sr-only">{t('you')}: </span>{message.content}</div></div>
  }
  return <div className="flex gap-3">
    <img src="/bee.svg" alt="" width="28" height="28" className="mt-0.5 size-7 shrink-0 rounded-full" />
    <div className="min-w-0 flex-1"><span className="sr-only">{name}: </span>{message.content ? <Markdown>{message.content}</Markdown> : null}</div>
  </div>
}

/** Tarefa avulsa ligada à mensagem: um chip; os detalhes ficam no painel de atividade. */
function TaskChip({ item, onOpen }: { item: Delegation; onOpen: () => void }) {
  const { t } = useTranslation('workspace')
  const live = ['queued', 'running'].includes(item.task.status)
  return <button type="button" onClick={onOpen} className="group inline-flex max-w-full cursor-pointer items-center gap-2 self-start rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground hover:text-foreground">
    {live ? <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" aria-hidden="true" /> : <ListTodo className="size-3.5 shrink-0" aria-hidden="true" />}
    <span className="min-w-0 truncate text-foreground">{item.task.title}</span>
    <span className="shrink-0">· {t(`status.${item.task.status}`)}</span>
    <ChevronRight className="size-3.5 shrink-0" aria-hidden="true" />
  </button>
}

/** Conversa no estilo assistente: uma rolagem, campo fixo embaixo, a abelha decide o que fazer. */
export function ChatView({ agent, onTaskDetails, onComputerActivity }: { agent: Agent; onTaskDetails: (taskId: string) => void; onComputerActivity?: () => void }) {
  const { t, i18n } = useTranslation('workspace')
  const [capabilities, setCapabilities] = useState<AgentCapabilities | null>(null)
  const [capabilityVersion, setCapabilityVersion] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    getAgentCapabilities(agent.id, controller.signal).then(setCapabilities, () => { if (!controller.signal.aborted) setCapabilities(null) })
    return () => controller.abort()
  }, [agent.id, agent.revision, capabilityVersion])
  // Plug and play: com modelo compatível, ativa as ferramentas recomendadas uma vez.
  const autoEnabled = useRef<string | null>(null)
  useEffect(() => {
    if (!capabilities || capabilities.available || !capabilities.tool_calls_supported || capabilities.recommended_installed || autoEnabled.current === agent.id) return
    autoEnabled.current = agent.id
    void enableRecommendedTools(agent.id, crypto.randomUUID()).then(() => setCapabilityVersion((value) => value + 1), () => undefined)
  }, [capabilities, agent.id])
  const useAgent = !!capabilities?.available

  const load = useCallback((signal: AbortSignal) => loadMessages(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const { resource, reload } = useResource(load)
  const loadDelegations = useCallback((signal: AbortSignal) => getDelegations(agent.id, agent.conversation_id, signal), [agent.id, agent.conversation_id])
  const monitor = useTaskPolling(loadDelegations, mergeDelegations)
  const delegations = monitor.data?.delegations ?? noDelegations

  // Recarrega as mensagens quando um turno do agente termina (só conclusões novas).
  const finished = delegations.filter((item) => item.task.execution_kind === 'agent_task' && !['queued', 'running', 'waiting_approval', 'waiting_resource'].includes(item.task.status)).map((item) => `${item.id}:${item.task.status}`).join(',')
  const seen = useRef<string | null>(null)
  const loaded = monitor.data !== null
  useEffect(() => { if (!loaded) return; if (seen.current !== null && seen.current !== finished) reload(); seen.current = finished }, [loaded, finished, reload])
  // O computador aparece ao lado quando a abelha começa a usá-lo.
  const computerTask = delegations.find((item) => ['queued', 'running'].includes(item.task.status) && item.task.agent_steps?.at(-1)?.tool.startsWith('computer_'))?.task.id
  useEffect(() => { if (computerTask) onComputerActivity?.() }, [computerTask, onComputerActivity])

  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [identity] = useState(commandIdentity)
  const agentIdentity = useRef<{ content: string; id: string } | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const follow = useRef(true)

  const draftRef = useRef(draft)
  useEffect(() => { draftRef.current = draft }, [draft])
  const dictation = useDictation(i18n.resolvedLanguage ?? 'pt-BR', (text) => { if (text) setDraft(draftRef.current ? `${draftRef.current.trimEnd()} ${text}` : text) })
  const [voiceReplies, setVoiceReplies] = useState(readVoicePreference)
  const spoken = useRef<string | null | undefined>(undefined)

  const list = useMemo(() => resource.state === 'ready' ? resource.data.messages.filter((message) => message.role !== 'tool').slice(-200) : [], [resource])
  useEffect(() => {
    if (resource.state !== 'ready') return
    const last = [...list].reverse().find((item) => item.role === 'assistant' && item.content)
    if (spoken.current !== undefined && voiceReplies && last && last.id !== spoken.current) speak(last.content ?? '', i18n.resolvedLanguage ?? 'pt-BR')
    spoken.current = last?.id ?? null
  }, [resource.state, list, voiceReplies, i18n.resolvedLanguage])
  // Rola só a lista; scrollIntoView também moveria ancestrais com overflow oculto (a tela inteira).
  useEffect(() => { const node = scroller.current; if (follow.current && node) node.scrollTop = node.scrollHeight }, [list, delegations])

  const ids = new Set(list.map((message) => message.id))
  const agentTurns = delegations.filter((item) => item.task.execution_kind === 'agent_task')
  // O turno da abelha nasce da mensagem do usuário que o originou e aparece logo depois dela.
  const turnFor = (message: Message) => message.role === 'user' ? agentTurns.find((item) => item.source_message_id === message.id) : undefined
  const chipsFor = (message: Message) => delegations.filter((item) => item.task.execution_kind !== 'agent_task' && item.source_message_id === message.id)
  // Sem a mensagem de origem na tela: só o turno recém-enviado (ainda não recarregado) aparece no fim;
  // turnos antigos, fora da janela, ficam no painel de atividade e nunca embaralham a ordem.
  const since = list[0] ? Date.parse(list[0].created_at) : -Infinity
  const orphanTurns = agentTurns.filter((item) => !ids.has(item.source_message_id) && Date.parse(item.task.created_at) > since).reverse()

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const content = draft.trim()
    if (busy || resource.state !== 'ready' || !content || agent.status !== 'active') return
    setBusy(true); setError(null); follow.current = true
    try {
      if (useAgent) {
        // Mesmo UUID enquanto o texto não mudar: reenvio reconcilia sem criar outra mensagem.
        if (agentIdentity.current?.content !== content) agentIdentity.current = { content, id: crypto.randomUUID() }
        await sendAgentMessage(agent.id, resource.data.conversation_id, content, agentIdentity.current.id, undefined, 'agent')
        agentIdentity.current = null
      } else {
        const requestId = identity.forPayload({ conversation_id: resource.data.conversation_id, content, allow_delegation: false })
        await sendChat(agent.id, resource.data.conversation_id, content, undefined, false, requestId)
        identity.reset()
      }
      setDraft('')
    } catch (failure) { setError(asApiError(failure)) } finally {
      setBusy(false); reload(); monitor.refresh(); inputRef.current?.focus()
    }
  }

  const empty = resource.state === 'ready' && list.length === 0 && delegations.length === 0
  return <div className="flex min-h-0 flex-1 flex-col">
    <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto" onScroll={(event) => { const node = event.currentTarget; follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80 }}>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6" role="log" aria-label={agent.name} aria-live="polite" aria-relevant="additions">
        {resource.state === 'loading' && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden="true" />{t('thinking')}</p>}
        {resource.state === 'error' && <div className="legacy-scope"><ErrorNotice error={resource.error} /><Button variant="outline" size="sm" onClick={reload}>{t('retry')}</Button></div>}
        {empty && <div className="flex flex-col items-center gap-3 py-16 text-center">
          <img src="/bee.svg" alt="" width="56" height="56" />
          <h2 className="text-2xl font-semibold">{t('emptyTitle')}</h2>
          <p className="max-w-md text-sm text-muted-foreground">{t('emptyHint')}</p>
          {useAgent && <div className="mt-2 flex flex-wrap justify-center gap-2">{(['suggestion1', 'suggestion2', 'suggestion3'] as const).map((key) => <Button key={key} variant="outline" size="sm" className="h-auto max-w-xs py-2 text-left whitespace-normal" onClick={() => { setDraft(t(key)); inputRef.current?.focus() }}>{t(key)}</Button>)}</div>}
        </div>}
        {list.map((message, index) => {
          const turn = turnFor(message)
          const live = turn ? ['queued', 'running'].includes(turn.task.status) : false
          // A resposta do agente vem na mensagem seguinte; os arquivos ficam logo abaixo dela.
          const previous = message.role !== 'user' ? [...list.slice(0, index)].reverse().find((item) => item.role === 'user') : undefined
          const answered = previous ? turnFor(previous) : undefined
          const replyFiles = answered && list.slice(list.indexOf(previous!) + 1).find((item) => item.role !== 'user')?.id === message.id ? answered : undefined
          const following = list[index + 1]
          const hasReply = !!turn && !!following && following.role !== 'user'
          return <div key={message.id} className="flex flex-col gap-3">
            <Bubble message={message} name={agent.name} />
            {turn && <div className={cn('flex gap-3', !live && 'pl-10')}>{live && <img src="/bee.svg" alt="" width="28" height="28" className="mt-0.5 size-7 shrink-0 rounded-full" />}<div className="min-w-0 flex-1"><AgentTurn task={turn.task} hasReply={!live} showFiles={!hasReply} onChanged={() => { monitor.refresh(); reload() }} onDetails={() => onTaskDetails(turn.task.id)} /></div></div>}
            {replyFiles && <div className="pl-10"><TurnFiles task={replyFiles.task} /></div>}
            {chipsFor(message).map((item) => <TaskChip key={item.id} item={item} onOpen={() => onTaskDetails(item.task.id)} />)}
          </div>
        })}
        {orphanTurns.map((item) => <div key={item.id} className="flex gap-3"><img src="/bee.svg" alt="" width="28" height="28" className="mt-0.5 size-7 shrink-0 rounded-full" /><div className="min-w-0 flex-1"><AgentTurn task={item.task} hasReply={false} onChanged={() => { monitor.refresh(); reload() }} onDetails={() => onTaskDetails(item.task.id)} /></div></div>)}
      </div>
    </div>
    <div className="shrink-0 border-t bg-background/80 px-4 pt-3 pb-4 backdrop-blur">
      <form className="mx-auto w-full max-w-3xl" onSubmit={(event) => { void submit(event) }}>
        {error && <div className="legacy-scope mb-2"><ErrorNotice error={error} /></div>}
        {capabilities && !capabilities.tool_calls_supported && <p className="mb-2 text-xs text-muted-foreground">{t('noTools')}</p>}
        {useAgent && capabilities?.worker && !capabilities.worker.available && <p className="mb-2 text-xs text-muted-foreground" role="status">{t('workerOffline')}</p>}
        <div className={cn('flex flex-col rounded-2xl border bg-card shadow-sm focus-within:ring-3 focus-within:ring-ring/40', busy && 'opacity-80')}>
          <label htmlFor="chat-input" className="sr-only">{t('placeholder', { name: agent.name })}</label>
          <Textarea id="chat-input" ref={inputRef} value={draft} rows={1} maxLength={16000} disabled={agent.status !== 'active'} placeholder={t('placeholder', { name: agent.name })} className="max-h-60 px-4 pt-3" aria-describedby="chat-enter-hint"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} />
          <div className="flex items-center gap-1 px-2 pb-2">
            {dictation.supported && <Tooltip label={t(dictation.listening ? 'stopDictation' : 'dictate')}><Button variant="ghost" size="icon-sm" aria-label={t(dictation.listening ? 'stopDictation' : 'dictate')} aria-pressed={dictation.listening} onClick={() => { if (dictation.listening) { dictation.stop() } else { dictation.start() } }}>{dictation.listening ? <MicOff /> : <Mic />}</Button></Tooltip>}
            {canSpeak() && <Tooltip label={t('readAloud')}><Button variant="ghost" size="icon-sm" aria-label={t('readAloud')} aria-pressed={voiceReplies} onClick={() => { setVoiceReplies(!voiceReplies); writeVoicePreference(!voiceReplies) }}>{voiceReplies ? <Volume2 /> : <VolumeX />}</Button></Tooltip>}
            {dictation.listening && <span className="truncate text-xs text-muted-foreground" role="status">{dictation.interim}</span>}
            <span id="chat-enter-hint" className="ml-auto hidden text-xs text-muted-foreground sm:inline">{t('enterHint')}</span>
            <Button type="submit" size="icon-sm" className="ml-auto rounded-full sm:ml-2" aria-label={t(busy ? 'sending' : 'send')} disabled={busy || resource.state !== 'ready' || !draft.trim() || agent.status !== 'active'}>{busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}</Button>
          </div>
        </div>
      </form>
    </div>
  </div>
}
