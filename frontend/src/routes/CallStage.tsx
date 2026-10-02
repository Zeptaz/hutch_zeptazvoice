import { useEffect, useRef, useState } from 'react'
import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react'
import { describeError } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { useI18n, type Translate } from '@/i18n/context'
import { cn } from '@/lib/utils'
import { CALL_LIMIT_MS, type CallError, type CallState, type VoiceCall } from '@/voice/call'

/** The call itself: one big orb that shows who is talking, plus time left, mute and hang up. */
export function CallStage({ call, state }: { call: VoiceCall; state: CallState }) {
  const { t } = useI18n()
  const { phase, activity, muted } = state
  const live = phase === 'live'
  const busy = phase === 'requesting' || phase === 'connecting'
  const status = live
    ? muted && activity === 'listening'
      ? t('voice.state.muted')
      : t(`voice.state.${activity}`)
    : busy
      ? t(`voice.state.${phase}`)
      : phase === 'ended'
        ? t('voice.state.ended')
        : t('voice.state.idle')

  return (
    <section aria-label={t('voice.start')} className="flex flex-col items-center gap-4 px-4 pt-6 pb-5 text-center">
      <Orb call={call} state={state} onStart={() => void call.start()} label={phase === 'ended' ? t('voice.callAgain') : t('voice.start')} />
      <div className="flex min-h-11 flex-col items-center gap-0.5">
        <p aria-live="polite" className="text-base font-semibold">
          {status}
        </p>
        {live && state.liveAt != null ? (
          <TimeLeft since={state.liveAt} t={t} />
        ) : (
          <p className="max-w-xs text-xs text-balance text-muted-foreground">{t('voice.hint')}</p>
        )}
      </div>
      {(live || busy) && (
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="lg"
            className="rounded-full"
            aria-pressed={muted}
            disabled={!live}
            onClick={() => call.setMuted(!muted)}
          >
            {muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}
            {muted ? t('voice.unmute') : t('voice.mute')}
          </Button>
          <Button variant="destructive" size="lg" className="rounded-full" onClick={() => call.stop()}>
            <PhoneOff aria-hidden /> {t('voice.end')}
          </Button>
        </div>
      )}
      <CallNotice state={state} t={t} />
      <p className="text-[11px] text-muted-foreground">{t('voice.privacy')}</p>
    </section>
  )
}

function CallNotice({ state, t }: { state: CallState; t: Translate }) {
  const text = state.error ? errorText(state.error, t) : state.phase === 'ended' && state.endReason ? endText(state.endReason, t) : null
  if (!text) return null
  return (
    <p role={state.error ? 'alert' : 'status'} className={cn('max-w-sm text-sm text-balance', state.error ? 'text-destructive' : 'text-muted-foreground')}>
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

function TimeLeft({ since, t }: { since: number; t: Translate }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])
  const left = Math.max(0, Math.ceil((CALL_LIMIT_MS - (now - since)) / 1000))
  const text = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`
  return <p className={cn('font-mono text-xs tabular-nums', left <= 20 ? 'text-destructive' : 'text-muted-foreground')}>{t('voice.timeLeft', { time: text })}</p>
}

const BARS = [0.55, 0.85, 1, 0.8, 0.5]

/**
 * Idle: an orange call button that breathes. Connecting: a spinning arc. Listening: a halo that
 * follows the microphone. Thinking: pulsing dots. Speaking: bars that follow Resolve's voice.
 * Levels are read every frame and written straight to the DOM, so React doesn't re-render at 60 fps.
 */
function Orb({ call, state, onStart, label }: { call: VoiceCall; state: CallState; onStart: () => void; label: string }) {
  const halo = useRef<HTMLSpanElement>(null)
  const bars = useRef<HTMLSpanElement>(null)
  const { phase, activity } = state
  const live = phase === 'live'

  useEffect(() => {
    if (!live) return
    let frame = 0
    let mic = 0
    let speaker = 0
    const tick = (time: number) => {
      const levels = call.levels
      mic += (levels.mic - mic) * 0.35
      speaker += (levels.speaker - speaker) * 0.4
      if (halo.current) halo.current.style.transform = `scale(${1 + mic * 0.45})`
      if (bars.current) {
        Array.from(bars.current.children).forEach((bar, i) => {
          const wobble = 0.6 + 0.4 * Math.sin(time / 110 + i * 1.7)
          ;(bar as HTMLElement).style.transform = `scaleY(${0.18 + Math.min(1, speaker * 1.6) * BARS[i] * wobble})`
        })
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [call, live])

  if (phase === 'idle' || phase === 'ended') {
    return (
      <div className="relative grid size-40 place-items-center">
        <span aria-hidden className="absolute inset-4 animate-orb-breathe rounded-full bg-primary/25" />
        <button
          type="button"
          onClick={onStart}
          className="relative grid size-28 place-items-center rounded-full bg-primary text-primary-foreground shadow-lg shadow-primary/30 transition-transform outline-none hover:scale-105 focus-visible:ring-4 focus-visible:ring-ring/50 active:scale-95"
        >
          <Phone aria-hidden className="size-10" />
          <span className="sr-only">{label}</span>
        </button>
      </div>
    )
  }

  return (
    <div aria-hidden className="relative grid size-40 place-items-center">
      <span
        ref={halo}
        className={cn(
          'absolute inset-5 rounded-full transition-colors duration-300',
          activity === 'speaking' ? 'bg-primary/20' : activity === 'thinking' ? 'bg-accent' : 'bg-primary/15',
        )}
      />
      {!live && <span className="absolute inset-3 animate-spin rounded-full border-4 border-muted border-t-primary [animation-duration:1.1s]" />}
      <span
        className={cn(
          'relative grid size-28 place-items-center rounded-full shadow-md transition-colors duration-300',
          live && activity === 'speaking' ? 'bg-primary text-primary-foreground' : live && activity === 'thinking' ? 'bg-accent text-accent-foreground' : 'bg-card text-foreground ring-1 ring-border',
          live && activity === 'thinking' && 'animate-thinking-ring',
        )}
      >
        {live && activity === 'speaking' ? (
          <span ref={bars} className="flex h-12 items-center gap-1.5">
            {BARS.map((_, i) => (
              <span key={i} className="h-full w-1.5 origin-center rounded-full bg-current" style={{ transform: 'scaleY(0.2)' }} />
            ))}
          </span>
        ) : live && activity === 'thinking' ? (
          <span className="flex items-center gap-1.5">
            {[0, 160, 320].map((d) => (
              <span key={d} className="size-2.5 animate-typing-dot rounded-full bg-current" style={{ animationDelay: `${d}ms` }} />
            ))}
          </span>
        ) : state.muted ? (
          <MicOff className="size-10 text-muted-foreground" />
        ) : (
          <Mic className="size-10" />
        )}
      </span>
    </div>
  )
}

/** Phones: a slim bar pinned to the top once the big call area scrolls away, so mute and hang up stay in reach. */
export function CallBar({ call, state }: { call: VoiceCall; state: CallState }) {
  const { t } = useI18n()
  const { phase, activity, muted } = state
  const live = phase === 'live'
  const status = live ? (muted && activity === 'listening' ? t('voice.state.muted') : t(`voice.state.${activity}`)) : t('voice.state.connecting')
  return (
    <div className="sticky top-0 z-10 flex animate-bubble-in items-center gap-3 border-b bg-background/95 px-4 py-2 backdrop-blur lg:hidden">
      <span
        aria-hidden
        className={cn(
          'grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-4',
          live && activity === 'speaking' ? 'bg-primary text-primary-foreground' : 'bg-accent text-accent-foreground',
          live && activity !== 'listening' && 'animate-thinking-ring',
        )}
      >
        {muted ? <MicOff /> : <Mic />}
      </span>
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-semibold">{status}</span>
        {live && state.liveAt != null && <TimeLeft since={state.liveAt} t={t} />}
      </div>
      <Button variant="outline" size="icon" className="rounded-full" aria-pressed={muted} disabled={!live} onClick={() => call.setMuted(!muted)} aria-label={muted ? t('voice.unmute') : t('voice.mute')}>
        {muted ? <MicOff aria-hidden /> : <Mic aria-hidden />}
      </Button>
      <Button variant="destructive" size="icon" className="rounded-full" onClick={() => call.stop()} aria-label={t('voice.end')}>
        <PhoneOff aria-hidden />
      </Button>
    </div>
  )
}
