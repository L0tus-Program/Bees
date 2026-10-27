import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { asApiError } from '../../api/client'
import type { ApiError } from '../../api/client'
import { createChannel, listChannels, newPairingCode, revokeChannel, updateChannel } from '../../api/channels'
import type { Channel, ChannelKind, ChannelMode, Pairing } from '../../api/channels'
import { ErrorNotice, LoadingNotice } from '../../components/Feedback'
import { confirmAction } from '../../components/confirmAction'

const MODES: ChannelMode[] = ['chat', 'agent', 'research']

export function ChannelsPanel({ agentId }: { agentId: string }) {
  const { t, i18n } = useTranslation('channels')
  const [channels, setChannels] = useState<Channel[] | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [pairing, setPairing] = useState<Pairing | null>(null)
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState<ChannelKind>('telegram')
  const [token, setToken] = useState('')
  const [target, setTarget] = useState('')
  const [name, setName] = useState('')
  const [mode, setMode] = useState<ChannelMode>('chat')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const time = (value: string) => new Intl.DateTimeFormat(i18n.language, { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))

  const load = useCallback(async (signal?: AbortSignal) => {
    try { setChannels(await listChannels(agentId, signal)) } catch (failure) { if (!signal?.aborted) setError(asApiError(failure)) }
  }, [agentId])
  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    const timer = setInterval(() => { void load() }, 10000)
    return () => { controller.abort(); clearInterval(timer) }
  }, [load])

  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError(null)
    try { await action(); await load() } catch (failure) { setError(asApiError(failure)) } finally { setBusy(false) }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await act(async () => {
      const result = await createChannel(agentId, { client_request_id: requestId, kind, token: token.trim(), mode, ...(name.trim() ? { name: name.trim() } : {}), ...(kind === 'discord' ? { target: target.trim() } : {}) })
      setPairing(result); setToken(''); setTarget(''); setName(''); setRequestId(crypto.randomUUID())
    })
  }

  const pairingFor = (channel: Channel) => pairing?.channel.id === channel.id ? pairing : null
  return (
    <section className="surface channels-panel" aria-labelledby="channels-title">
      <h2 id="channels-title">{t('title')}</h2>
      <p className="quiet-note">{t('scope')}</p>
      {error && <ErrorNotice error={error} />}
      {channels === null && !error && <LoadingNotice />}
      {channels?.length === 0 && <p>{t('empty')}</p>}
      <ul className="channel-list">
        {channels?.map((channel) => {
          const current = pairingFor(channel)
          return <li key={channel.id} className="channel-card">
            <div className="channel-heading"><strong>{channel.name}</strong><span className="provider-label">{t(`kind_${channel.kind}`)}</span><span>{t(`status_${channel.status}`)}</span></div>
            {channel.peer_name && <small>{t('pairedWith', { name: channel.peer_name })}</small>}
            {channel.last_activity_at && <small> · {t('lastActivity', { time: time(channel.last_activity_at) })}</small>}
            {channel.reason_code && <p className="transfer-notice">{t(`reason_${channel.reason_code}`, { defaultValue: t('reason_other', { code: channel.reason_code }) })}</p>}
            {current?.replayed && <p className="transfer-notice">{t('replayed')}</p>}
            {current?.pairing_code && <div className="channel-pairing" role="status">
              <p><strong>{t('pairTitle')}</strong> — {t('pairCode', { code: current.pairing_code })}</p>
              <p>{t(channel.kind === 'telegram' ? 'pairTelegram' : 'pairDiscord', { code: current.pairing_code })}</p>
              {current.pairing_link && <a className="button secondary" href={current.pairing_link} target="_blank" rel="noreferrer">{t('openTelegram')}</a>}
              {current.channel.pairing_expires_at && <small>{t('pairExpires', { time: time(current.channel.pairing_expires_at) })}</small>}
            </div>}
            <div className="row-actions">
              <label className="inline-select"><span>{t('mode')}</span><select value={channel.mode} disabled={busy || channel.status === 'failed'} onChange={(event) => { void act(() => updateChannel(channel, { mode: event.target.value as ChannelMode })) }}>{MODES.map((item) => <option key={item} value={item}>{t(`mode_${item}`)}</option>)}</select></label>
              {channel.status !== 'failed' && <button type="button" className="button secondary" disabled={busy} onClick={() => { void act(async () => setPairing(await newPairingCode(channel))) }}>{t('newCode')}</button>}
              {(channel.status === 'active' || channel.status === 'pending') && <button type="button" className="text-button" disabled={busy} onClick={() => { void act(() => updateChannel(channel, { paused: true })) }}>{t('pause')}</button>}
              {channel.status === 'paused' && <button type="button" className="text-button" disabled={busy} onClick={() => { void act(() => updateChannel(channel, { paused: false })) }}>{t('resume')}</button>}
              <button type="button" className="text-button danger" disabled={busy} onClick={() => { void confirmAction(t('revokeConfirm')).then((ok) => { if (ok) void act(() => revokeChannel(channel)) }) }}>{t('revoke')}</button>
            </div>
          </li>
        })}
      </ul>
      <form className="channel-form" onSubmit={(event) => { void submit(event) }}>
        <h3>{t('add')}</h3>
        <fieldset disabled={busy}>
          <label className="field" htmlFor="channel-kind"><span>{t('kind')}</span><select id="channel-kind" value={kind} onChange={(event) => setKind(event.target.value as ChannelKind)}><option value="telegram">{t('kind_telegram')}</option><option value="discord">{t('kind_discord')}</option></select></label>
          <label className="field" htmlFor="channel-token"><span>{t('token')}</span><input id="channel-token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} required minLength={20} maxLength={200} aria-describedby="channel-token-help" /><small id="channel-token-help">{t(`tokenHelp_${kind}`)}</small></label>
          {kind === 'discord' && <label className="field" htmlFor="channel-target"><span>{t('target')}</span><input id="channel-target" inputMode="numeric" pattern="[0-9]{5,25}" value={target} onChange={(event) => setTarget(event.target.value)} required aria-describedby="channel-target-help" /><small id="channel-target-help">{t('targetHelp')}</small></label>}
          <label className="field" htmlFor="channel-name"><span>{t('name')}</span><input id="channel-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={100} /></label>
          <label className="field" htmlFor="channel-mode"><span>{t('mode')}</span><select id="channel-mode" value={mode} onChange={(event) => setMode(event.target.value as ChannelMode)}>{MODES.map((item) => <option key={item} value={item}>{t(`mode_${item}`)}</option>)}</select></label>
          <button type="submit" className="button primary">{t(busy ? 'creating' : 'create')}</button>
        </fieldset>
        <p className="quiet-note">{t('workerNote')}</p>
      </form>
    </section>
  )
}

export default ChannelsPanel
