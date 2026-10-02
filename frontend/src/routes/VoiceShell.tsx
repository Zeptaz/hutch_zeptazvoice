import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { MessageCircleWarning, Mic, MicOff, PhoneOff, Timer, Volume2, Zap } from 'lucide-react'
import { API_MODE, newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { Decision, OperationView, SessionView, VoiceProposal } from '@/api/types'
import { ErrorState, LoadingState } from '@/components/states'
import { OperationBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { hasMessage, useI18n, type Translate } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { CALL_LIMIT_MS, VoiceCall, type CallError, type CallState } from '@/voice/call'
import { Panel } from './VoicePage'

const conversationKey = (sessionId: string) => `hutch-voice.conversation.${sessionId}`
const POLL_MS = 1000
const POLL_LIMIT_MS = 60_000
const SETTLED = new Set(['SUCCEEDED', 'FAILED', 'REVIEW_REQUIRED'])

/** Opens (or reopens) this session's Resolve conversation, then hands over to the call panel. */
export function VoiceShell({ session }: { session: SessionView }) {
  const { language, t } = useI18n()
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const openingLanguage = useRef(language)
  const createKey = useRef(newId())

  useEffect(() => {
    let cancelled = false
    const open = async () => {
      let saved: string | null = null
      try {
        saved = sessionStorage.getItem(conversationKey(session.id))
      } catch {
        /* storage blocked: start a new conversation */
      }
      if (saved) {
        try {
          return (await customerApi.getConversation(saved)).id
        } catch (e) {
          if (!(isApiError(e) && e.status === 404)) throw e
        }
      }
      const created = await customerApi.createConversation(openingLanguage.current, createKey.current)
      try {
        sessionStorage.setItem(conversationKey(session.id), created.id)
      } catch {
        /* a refresh will start a new conversation */
      }
      return created.id
    }
    open()
      .then((id) => !cancelled && setConversationId(id))
      .catch((e) => !cancelled && setError(e))
    return () => {
      cancelled = true
    }
  }, [session.id, attempt])

  if (error)
    return (
      <Panel>
        <ErrorState
          title={t('voice.openFailed')}
          error={error}
          onRetry={() => {
            setError(null)
            setAttempt((n) => n + 1)
          }}
        />
      </Panel>
    )
  if (!conversationId)
    return (
      <Panel>
        <LoadingState label={t('voice.opening')} rows={2} />
      </Panel>
    )
  return <CallPanel conversationId={conversationId} />
}

type Caption = { user: string | null; reply: { id: string; text: string; fallback: boolean } | null }
type Offer = { proposal: VoiceProposal; answer: 'idle' | 'sending'; error: unknown }

const IDLE_STATE = new VoiceCall('').getState()

function CallPanel({ conversationId }: { conversationId: string }) {
  const { language, t } = useI18n()
  const call = useMemo(() => new VoiceCall(conversationId), [conversationId])
  const state = useSyncExternalStore(call.subscribe, call.getState, () => IDLE_STATE)
  const [caption, setCaption] = useState<Caption>({ user: null, reply: null })
  const [offer, setOffer] = useState<Offer | null>(null)
  const [operation, setOperation] = useState<OperationView | null>(null)
  const glowRef = useRef<HTMLDivElement>(null)

  /** Follow the latest action Resolve started until the provider settles it. UNKNOWN keeps polling. */
  const watchLatestOperation = useCallback(async () => {
    try {
      const conversation = await customerApi.getConversation(conversationId)
      const id = conversation.messages.findLast((m) => m.result?.operation_ids.length)?.result?.operation_ids.at(-1)
      if (!id) return
      const started = Date.now()
      for (;;) {
        const op = await customerApi.getOperation(id)
        setOperation(op)
        if (SETTLED.has(op.status) || Date.now() - started > POLL_LIMIT_MS) return
        await new Promise((r) => setTimeout(r, POLL_MS))
      }
    } catch {
      /* the spoken reply already said what Resolve knew */
    }
  }, [conversationId])

  useEffect(() => {
    call.setHandlers({
      onGreeting: (text) => setCaption({ user: null, reply: { id: 'greeting', text, fallback: false } }),
      onTranscript: (text) => setCaption({ user: text, reply: null }),
      onResolveResult: (r) => {
        setCaption((c) => ({ ...c, reply: { id: r.response_id, text: r.reply_text, fallback: false } }))
        setOffer(r.proposal ? { proposal: r.proposal, answer: 'idle', error: null } : null)
        if (r.operation_status) void watchLatestOperation()
      },
      onFallback: (id, text) => setCaption((c) => (c.reply?.id === id ? { ...c, reply: { id, text, fallback: true } } : c)),
    })
    return () => call.dispose()
  }, [call, watchLatestOperation])

  // The panel's glow follows whoever is talking.
  const live = state.phase === 'live'
  useEffect(() => {
    const el = glowRef.current
    if (!el) return
    if (!live) {
      el.style.opacity = ''
      return
    }
    let frame = 0
    let level = 0
    const tick = () => {
      const { mic, speaker } = call.levels
      level += (Math.max(mic, speaker) - level) * 0.2
      el.style.opacity = String(0.6 + level * 0.4)
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [call, live])

  /** Explicit Yes/No by tap, for when an offer can't be confirmed by voice. Resolve receives it as a text turn. */
  const answerByTap = async (decision: Decision) => {
    if (!offer || offer.answer !== 'idle') return
    setOffer({ ...offer, answer: 'sending', error: null })
    try {
      const conversation = await customerApi.getConversation(conversationId)
      const result = await customerApi.sendMessage(conversationId, {
        client_turn_id: newId(),
        expected_version: conversation.version,
        language,
        input: { type: 'action_decision', proposal_id: offer.proposal.id, proposal_hash: offer.proposal.proposal_hash, decision },
      })
      setCaption({
        user: decision === 'ACCEPT' ? t('confirm.yes') : t('confirm.no'),
        reply: { id: result.message_id, text: result.reply_text, fallback: false },
      })
      setOffer(null)
      if (result.operation_ids.length) void watchLatestOperation()
    } catch (error) {
      setOffer({ ...offer, answer: 'idle', error })
    }
  }

  const voiceStatus = state.proposal && state.proposal.data.id === offer?.proposal.id ? state.proposal.status : 'text-only'

  return (
    <>
      <Panel glowRef={glowRef}>
        <div className="flex flex-col items-center gap-7 text-center">
          <StatusLine state={state} t={t} />
          <Orb call={call} state={state} label={state.phase === 'ended' ? t('voice.callAgain') : t('voice.start')} />
          <Captions caption={caption} state={state} t={t} />
          {offer && <OfferBox offer={offer} voiceStatus={voiceStatus} live={live} onAnswer={(d) => void answerByTap(d)} t={t} />}
          {operation && <ActionStatus op={operation} t={t} />}
          <Controls call={call} state={state} t={t} />
        </div>
      </Panel>
      <TrySaying live={live} t={t} />
    </>
  )
}

function StatusLine({ state, t }: { state: CallState; t: Translate }) {
  const { phase, activity, muted } = state
  const value =
    phase === 'live'
      ? muted && activity === 'listening'
        ? t('voice.state.muted')
        : t(`voice.state.${activity}`)
      : phase === 'requesting' || phase === 'connecting'
        ? t(`voice.state.${phase}`)
        : phase === 'ended'
          ? t('voice.state.ended')
          : t('voice.state.idle')
  return (
    <p aria-live="polite" className="text-xs font-semibold tracking-[0.28em] uppercase">
      {t('voice.status')}:<span className="ml-3 text-primary">{value}</span>
    </p>
  )
}

const BARS = [0.5, 0.8, 1, 0.75, 0.45]

/**
 * A white disc, as in the reference. Idle it is the call button and breathes; connecting it is
 * ringed by a spinner; listening, a ring follows the microphone; thinking, dots pulse; speaking,
 * orange bars follow Resolve's voice. Levels are written straight to the DOM every frame.
 */
function Orb({ call, state, label }: { call: VoiceCall; state: CallState; label: string }) {
  const ring = useRef<HTMLSpanElement>(null)
  const bars = useRef<HTMLSpanElement>(null)
  const { phase, activity, muted } = state
  const live = phase === 'live'
  const idle = phase === 'idle' || phase === 'ended'

  useEffect(() => {
    if (!live) return
    let frame = 0
    let mic = 0
    let speaker = 0
    const tick = (time: number) => {
      const levels = call.levels
      mic += (levels.mic - mic) * 0.35
      speaker += (levels.speaker - speaker) * 0.4
      if (ring.current) ring.current.style.transform = `scale(${1 + mic * 0.5})`
      if (bars.current)
        Array.from(bars.current.children).forEach((bar, i) => {
          const wobble = 0.6 + 0.4 * Math.sin(time / 110 + i * 1.7)
          ;(bar as HTMLElement).style.transform = `scaleY(${0.2 + Math.min(1, speaker * 1.6) * BARS[i] * wobble})`
        })
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [call, live])

  const face =
    live && activity === 'speaking' ? (
      <span ref={bars} className="flex h-14 items-center gap-2 text-primary">
        {BARS.map((_, i) => (
          <span key={i} className="h-full w-2 rounded-full bg-current" style={{ transform: 'scaleY(0.2)' }} />
        ))}
      </span>
    ) : live && activity === 'thinking' ? (
      <span className="flex items-center gap-2">
        {[0, 160, 320].map((d) => (
          <span key={d} className="size-3 animate-typing-dot rounded-full bg-current" style={{ animationDelay: `${d}ms` }} />
        ))}
      </span>
    ) : muted ? (
      <MicOff className="size-12" />
    ) : (
      <Mic className="size-12" />
    )

  return (
    <div className="relative grid size-56 place-items-center">
      {idle && <span aria-hidden className="absolute inset-6 animate-orb-breathe rounded-full bg-white/15" />}
      {live && <span ref={ring} aria-hidden className="absolute inset-5 rounded-full border-2 border-white/25 bg-white/5" />}
      {(phase === 'requesting' || phase === 'connecting') && (
        <span aria-hidden className="absolute inset-3 animate-spin rounded-full border-4 border-white/10 border-t-primary [animation-duration:1.1s]" />
      )}
      {idle ? (
        <button
          type="button"
          onClick={() => void call.start()}
          className="relative grid size-44 place-items-center rounded-full bg-white text-neutral-950 shadow-xl shadow-black/40 transition-transform outline-none hover:scale-[1.03] focus-visible:ring-4 focus-visible:ring-primary/60 active:scale-95"
        >
          <Mic aria-hidden className="size-12" />
          <span className="sr-only">{label}</span>
        </button>
      ) : (
        <span
          aria-hidden
          className={cn(
            'relative grid size-44 place-items-center rounded-full bg-white text-neutral-950 shadow-xl shadow-black/40 transition-opacity',
            !live && 'opacity-80',
            live && activity === 'thinking' && 'animate-thinking-ring',
          )}
        >
          {face}
        </span>
      )}
    </div>
  )
}

/** The latest exchange as captions: what the caller said, then Resolve's reply. */
function Captions({ caption, state, t }: { caption: Caption; state: CallState; t: Translate }) {
  const notice = state.error ? errorText(state.error, t) : state.phase === 'ended' && state.endReason ? endText(state.endReason, t) : null
  if (!caption.user && !caption.reply) {
    return (
      <div className="flex flex-col gap-2">
        <p className="max-w-xs text-sm text-balance text-muted-foreground">{state.phase === 'live' ? t('voice.hint') : t('voice.help.idle')}</p>
        {notice && <Notice text={notice} error={!!state.error} />}
      </div>
    )
  }
  return (
    <div className="flex w-full flex-col gap-3" aria-live="polite">
      {caption.user && (
        <p key={`u-${caption.user}`} className="animate-bubble-in text-sm text-muted-foreground">
          <span className="font-semibold text-foreground/80">{t('voice.you')}: </span>“{caption.user}”
        </p>
      )}
      {caption.reply && (
        <p key={caption.reply.id} className="animate-bubble-in text-lg leading-relaxed font-medium text-balance">
          {caption.reply.text}
        </p>
      )}
      {caption.reply?.fallback && <p className="text-xs text-muted-foreground">{t('voice.fallback')}</p>}
      {notice && <Notice text={notice} error={!!state.error} />}
    </div>
  )
}

function Notice({ text, error }: { text: string; error: boolean }) {
  return (
    <p role={error ? 'alert' : 'status'} className={cn('text-sm text-balance', error ? 'text-destructive' : 'text-muted-foreground')}>
      {text}
    </p>
  )
}

function errorText(error: CallError, t: Translate) {
  if (error.kind === 'mic') return error.mic === 'missing' ? t('voice.err.micMissing') : error.mic === 'unsupported' ? t('voice.err.unsupported') : t('voice.err.mic')
  if (error.kind === 'grant') return t('voice.err.grant', { reason: describeError(error.error, t) })
  if (error.code === 'audio_limit_reached') return t('voice.err.audioLimit')
  if (error.code.startsWith('voice_')) return t('voice.err.unavailable')
  return t('voice.err.generic')
}

function endText(reason: string, t: Translate) {
  if (reason === 'resolve_requested' || reason === 'session_limit' || reason === 'user') return t(`voice.ended.${reason}`)
  return t('voice.ended.disconnected')
}

/**
 * The offer Resolve made. By voice it counts only after it was read aloud and acknowledged; when that
 * isn't possible, or after the call, explicit Yes/No buttons answer it. Nothing is preselected.
 */
function OfferBox({
  offer,
  voiceStatus,
  live,
  onAnswer,
  t,
}: {
  offer: Offer
  voiceStatus: NonNullable<CallState['proposal']>['status']
  live: boolean
  onAnswer: (d: Decision) => void
  t: Translate
}) {
  const p = offer.proposal
  const left = useCountdown(p.expires_at)
  const expired = left === '0:00'
  const byTap = !live || voiceStatus === 'text-only' || voiceStatus === 'interrupted'
  const voiceLine =
    live && voiceStatus !== 'text-only'
      ? voiceStatus === 'reading'
        ? { icon: <Volume2 />, text: t('voice.proposal.reading') }
        : voiceStatus === 'awaiting'
          ? { icon: <Mic />, text: t('voice.proposal.listening') }
          : { icon: <Volume2 />, text: t('voice.proposal.interrupted') }
      : live
        ? { icon: null, text: t('voice.proposal.notByVoice') }
        : null
  return (
    <section aria-label={t('voice.proposal.title')} className="w-full animate-bubble-in rounded-2xl border border-primary/40 bg-white/[0.04] p-4 text-left">
      <p className="text-[11px] font-semibold tracking-[0.2em] text-primary uppercase">{t('voice.proposal.title')}</p>
      <p className="mt-2 font-semibold">{hasMessage(`action.${p.action_type}`) ? t(`action.${p.action_type}`) : humanize(p.action_type)}</p>
      <p className="text-sm">{p.target_label}</p>
      <p className="mt-1.5 text-sm text-muted-foreground">{p.consequences}</p>
      <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Timer aria-hidden className="size-3.5" />
        {expired ? t('confirm.expired') : t('confirm.validUntil', { time: formatTime(p.expires_at), left })}
      </p>
      {voiceLine && (
        <p
          aria-live="polite"
          className={cn(
            'mt-3 flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium [&_svg]:size-4 [&_svg]:shrink-0',
            voiceStatus === 'awaiting' ? 'bg-primary text-primary-foreground' : 'bg-white/5',
          )}
        >
          {voiceLine.icon && (
            <span aria-hidden className={cn(voiceStatus !== 'interrupted' && 'animate-pulse')}>
              {voiceLine.icon}
            </span>
          )}
          {voiceLine.text}
        </p>
      )}
      {byTap && !expired && (
        <div className="mt-3 flex flex-col gap-2">
          {live && <p className="text-xs text-muted-foreground">{t('voice.offer.answerByTap')}</p>}
          <div className="flex flex-wrap gap-2" role="group" aria-label={t('confirm.group')}>
            <Button className="rounded-full" disabled={offer.answer !== 'idle'} onClick={() => onAnswer('ACCEPT')}>
              {t('confirm.yes')}
            </Button>
            <Button variant="outline" className="rounded-full border-white/15 bg-white/5" disabled={offer.answer !== 'idle'} onClick={() => onAnswer('DECLINE')}>
              {t('confirm.no')}
            </Button>
          </div>
          {offer.error != null && (
            <p role="alert" className="text-xs text-destructive">
              {describeError(offer.error, t)}
            </p>
          )}
        </div>
      )}
    </section>
  )
}

function ActionStatus({ op, t }: { op: OperationView; t: Translate }) {
  const title = hasMessage(`op.${op.action_type}`) ? t(`op.${op.action_type}`) : humanize(op.action_type)
  const text = op.status === 'SUCCEEDED' && hasMessage(`op.done.${op.action_type}`) ? t(`op.done.${op.action_type}`) : t(`op.${op.status}`)
  return (
    <div aria-live="polite" className="flex w-full animate-bubble-in flex-col gap-1 rounded-2xl bg-white/[0.04] p-4 text-left">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold">{title}</p>
        <OperationBadge status={op.status} />
      </div>
      <p className="text-sm text-muted-foreground">{text}</p>
    </div>
  )
}

function Controls({ call, state, t }: { call: VoiceCall; state: CallState; t: Translate }) {
  const live = state.phase === 'live'
  const busy = state.phase === 'requesting' || state.phase === 'connecting'
  if (!live && !busy) return null
  return (
    <div className="flex flex-col items-center gap-3">
      {live && state.liveAt != null && <TimeLeft since={state.liveAt} t={t} />}
      <div className="flex flex-wrap items-center justify-center gap-3">
        <Button
          variant="outline"
          size="lg"
          className="rounded-full border-white/15 bg-white/5"
          aria-pressed={state.muted}
          disabled={!live}
          onClick={() => call.setMuted(!state.muted)}
        >
          {state.muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}
          {state.muted ? t('voice.unmute') : t('voice.mute')}
        </Button>
        <Button size="lg" className="rounded-full bg-destructive text-white hover:bg-destructive/90" onClick={() => call.stop()}>
          <PhoneOff aria-hidden /> {t('voice.end')}
        </Button>
      </div>
    </div>
  )
}

function TimeLeft({ since, t }: { since: number; t: Translate }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const left = Math.max(0, Math.ceil((CALL_LIMIT_MS - (now - since)) / 1000))
  const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`
  return (
    <p className={cn('font-mono text-xs tabular-nums', left <= 20 ? 'text-destructive' : 'text-muted-foreground')}>
      {t('voice.timeLeft', { time: text })}
    </p>
  )
}

/** "m:ss" until an ISO time, updated every second. */
function useCountdown(until: string) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const s = Math.max(0, Math.ceil((Date.parse(until) - now) / 1000))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

const EXAMPLES = ['voice.example.1', 'voice.example.2', 'voice.example.3'] as const
type MockVoice = typeof import('@/voice/mockSocket').mockVoice

/**
 * Example openers under the panel. In mock mode, while a call is live, they also stand in for the
 * caller's voice (plus answers, barge-in and a failing speech check), since mock mode has no microphone.
 */
function TrySaying({ live, t }: { live: boolean; t: Translate }) {
  const mock = API_MODE === 'mock' && live
  const [failCheck, setFailCheck] = useState(false)
  // Loaded on demand so live builds never include the mock Voice.
  const run = (fn: (m: MockVoice) => void) => void import('@/voice/mockSocket').then(({ mockVoice }) => fn(mockVoice))
  const say = (text: string) => run((m) => m.say(text))
  return (
    <section aria-label={t('voice.trySaying')} className="flex flex-col gap-3">
      <h2 className="text-center text-xs font-semibold tracking-[0.28em] text-muted-foreground uppercase">{t('voice.trySaying')}</h2>
      <ul className="flex flex-col gap-2">
        {EXAMPLES.map((key) => (
          <li key={key}>
            {mock ? (
              <button
                type="button"
                onClick={() => say(t(key))}
                className="w-full rounded-2xl border border-white/10 bg-card/60 px-4 py-3 text-left text-sm transition-colors hover:border-primary/50 hover:bg-card focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:outline-none"
              >
                “{t(key)}”
              </button>
            ) : (
              <p className="rounded-2xl border border-white/10 bg-card/40 px-4 py-3 text-sm text-muted-foreground">“{t(key)}”</p>
            )}
          </li>
        ))}
      </ul>
      {mock && (
        <div className="flex flex-col gap-2 rounded-2xl border border-dashed border-white/15 p-3">
          <p className="text-xs text-muted-foreground">{t('voice.mock.note')}</p>
          <div className="flex flex-wrap gap-1.5">
            {['Yes, go ahead', 'No, leave it', 'Thanks, that is all'].map((line) => (
              <Button key={line} variant="outline" size="sm" className="rounded-full border-white/15 bg-white/5" onClick={() => say(line)}>
                “{line}”
              </Button>
            ))}
            <Button variant="ghost" size="sm" onClick={() => run((m) => m.interrupt())}>
              <Zap aria-hidden /> Talk over the reply
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-pressed={failCheck}
              onClick={() =>
                run((m) => {
                  m.failSpeechCheck = !failCheck
                  setFailCheck(!failCheck)
                })
              }
            >
              <MessageCircleWarning aria-hidden /> {failCheck ? 'Speech check fails' : 'Speech check passes'}
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}
