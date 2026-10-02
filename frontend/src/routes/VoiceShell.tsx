import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Keyboard, Mic, SendHorizontal, ShieldCheck, Timer, Volume2 } from 'lucide-react'
import { API_MODE, newId } from '@/api/client'
import { customerApi } from '@/api/endpoints'
import { describeError, isApiError } from '@/api/errors'
import type { ConversationView, Decision, OperationView, ProposalView, SessionView, TurnInput, TurnResult } from '@/api/types'
import { CardFrame, ChatCard, CitationList } from '@/cards/ChatCards'
import { ConfirmationCard, type ProposalState } from '@/cards/ConfirmationCard'
import { OperationTracker } from '@/cards/OperationTracker'
import { Bubble, Typing } from '@/components/chat'
import { ErrorState, LoadingState } from '@/components/states'
import { StatusBadge } from '@/components/StatusBadge'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { hasMessage, useI18n } from '@/i18n/context'
import { formatTime, humanize } from '@/lib/format'
import { cn } from '@/lib/utils'
import { VoiceCall, type CallState, type ProposalStatus } from '@/voice/call'
import { CallBar, CallStage } from './CallStage'
import { VoiceHeader } from './VoicePage'

const MAX_TEXT = 4000
// Mock-only panel, split out so live builds never load the mock Voice or its fixtures.
const MockCaller = lazy(() => import('./MockCaller'))

/** One line of the thread. Voice and typed turns share it, as they share one Resolve conversation. */
type ThreadItem = {
  id: string
  speaker: 'USER' | 'ASSISTANT'
  text: string
  time?: string
  spoken?: boolean
  fallback?: boolean
  pending?: boolean
  animate?: boolean
  result?: TurnResult | null
}

const conversationKey = (sessionId: string) => `hutch-voice.conversation.${sessionId}`

function readSaved(sessionId: string) {
  try {
    return sessionStorage.getItem(conversationKey(sessionId))
  } catch {
    return null
  }
}

function fromConversation(c: ConversationView): ThreadItem[] {
  return c.messages.map((m) => ({
    id: m.id,
    speaker: m.speaker,
    text: m.body,
    time: m.created_at,
    result: m.speaker === 'ASSISTANT' ? m.result : null,
  }))
}

export function VoiceShell({ session, onSignOut }: { session: SessionView; onSignOut: () => void }) {
  const { language, t } = useI18n()
  const [conversation, setConversation] = useState<ConversationView | null>(null)
  const [thread, setThread] = useState<ThreadItem[]>([])
  const [loadError, setLoadError] = useState<unknown>(null)
  const [attempt, setAttempt] = useState(0)
  const openingLanguage = useRef(language)
  const createKey = useRef(newId())

  // Reopen this session's conversation after a refresh, otherwise start one. Voice and text share it.
  useEffect(() => {
    let cancelled = false
    const open = async () => {
      const saved = readSaved(session.id)
      if (saved) {
        try {
          return await customerApi.getConversation(saved)
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
      return created
    }
    open()
      .then((c) => {
        if (cancelled) return
        setConversation(c)
        setThread(fromConversation(c))
      })
      .catch((e) => !cancelled && setLoadError(e))
    return () => {
      cancelled = true
    }
  }, [session.id, attempt])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <VoiceHeader onSignOut={onSignOut} />
      {loadError ? (
        <main className="mx-auto w-full max-w-md pt-10">
          <ErrorState
            title={t('voice.openFailed')}
            error={loadError}
            onRetry={() => {
              setLoadError(null)
              setAttempt((n) => n + 1)
            }}
          />
        </main>
      ) : !conversation ? (
        <main className="mx-auto w-full max-w-md pt-10">
          <LoadingState label={t('voice.opening')} rows={2} />
        </main>
      ) : (
        <CallView conversation={conversation} setConversation={setConversation} thread={thread} setThread={setThread} />
      )}
    </div>
  )
}

const IDLE_CALL_STATE = new VoiceCall('').getState()

function CallView({
  conversation,
  setConversation,
  thread,
  setThread,
}: {
  conversation: ConversationView
  setConversation: (c: ConversationView) => void
  thread: ThreadItem[]
  setThread: React.Dispatch<React.SetStateAction<ThreadItem[]>>
}) {
  const { language, t } = useI18n()
  const call = useMemo(() => new VoiceCall(conversation.id), [conversation.id])
  const callState = useSyncExternalStore(call.subscribe, call.getState, () => IDLE_CALL_STATE)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<unknown>(null)
  const [decisions, setDecisions] = useState<Record<string, ProposalState>>({})
  // Phones scroll the call and thread as one column; wide screens scroll the thread beside the call.
  const pageRef = useRef<HTMLDivElement>(null)
  const threadRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<HTMLElement>(null)
  const [stageInView, setStageInView] = useState(true)
  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return
    const observer = new IntersectionObserver(([entry]) => setStageInView(entry.isIntersecting), { root: pageRef.current, threshold: 0.15 })
    observer.observe(stage)
    return () => observer.disconnect()
  }, [])
  const conversationRef = useRef(conversation)
  useEffect(() => {
    conversationRef.current = conversation
  }, [conversation])

  const add = useCallback((item: ThreadItem) => setThread((items) => [...items, item]), [setThread])

  /** Pull the canonical conversation and attach Resolve's cards to the matching reply. */
  const refresh = useCallback(
    async (replyId?: string, replyText?: string) => {
      try {
        const c = await customerApi.getConversation(conversationRef.current.id)
        setConversation(c)
        if (replyId && replyText) {
          const message = c.messages.findLast(
            (m) => m.speaker === 'ASSISTANT' && (m.result?.reply_text === replyText || m.body === replyText),
          )
          if (message?.result) setThread((items) => items.map((it) => (it.id === replyId ? { ...it, result: message.result } : it)))
        }
      } catch {
        /* the thread keeps what the call already showed */
      }
    },
    [setConversation, setThread],
  )

  useEffect(() => {
    call.setHandlers({
      onGreeting: (text) => add({ id: newId(), speaker: 'ASSISTANT', text, spoken: true, animate: true }),
      onTranscript: (text) => add({ id: newId(), speaker: 'USER', text, spoken: true, animate: true, time: new Date().toISOString() }),
      onResolveResult: (r) => {
        add({ id: r.response_id, speaker: 'ASSISTANT', text: r.reply_text, spoken: true, animate: true, time: new Date().toISOString() })
        void refresh(r.response_id, r.reply_text)
      },
      onFallback: (id, text) => setThread((items) => items.map((it) => (it.id === id ? { ...it, text, fallback: true } : it))),
    })
    return () => call.dispose()
  }, [call, add, refresh, setThread])

  // Keep the newest turn in view.
  const thinking = callState.phase === 'live' && callState.activity === 'thinking'
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const smooth = document.visibilityState === 'visible' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches
      for (const el of [pageRef.current, threadRef.current]) {
        if (el && el.scrollHeight > el.clientHeight) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
      }
    })
    return () => cancelAnimationFrame(frame)
  }, [thread.length, thinking, sending])

  const sendTurn = async (input: TurnInput, label: string) => {
    const pendingId = newId()
    add({ id: pendingId, speaker: 'USER', text: label, pending: true, animate: true })
    setSending(true)
    setSendError(null)
    try {
      const result = await customerApi.sendMessage(conversation.id, {
        client_turn_id: newId(),
        expected_version: conversation.version,
        language,
        input,
      })
      setThread((items) => [
        ...items.map((it) => (it.id === pendingId ? { ...it, pending: false, time: new Date().toISOString() } : it)),
        { id: result.message_id, speaker: 'ASSISTANT', text: result.reply_text, result, animate: true, time: new Date().toISOString() },
      ])
      await refresh()
      return true
    } catch (e) {
      setThread((items) => items.filter((it) => it.id !== pendingId))
      setSendError(e)
      if (isApiError(e) && (e.status === 409 || e.status === 422)) await refresh()
      return false
    } finally {
      setSending(false)
    }
  }

  const submitText = () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    void sendTurn({ type: 'text', text }, text).then((ok) => !ok && setDraft(text))
  }

  const decide = async (proposal: ProposalView, decision: Decision) => {
    if (sending) return
    setDecisions((d) => ({ ...d, [proposal.id]: { kind: 'submitting', decision } }))
    const ok = await sendTurn(
      { type: 'action_decision', proposal_id: proposal.id, proposal_hash: proposal.proposal_hash, decision },
      decision === 'ACCEPT' ? t('confirm.yes') : t('confirm.no'),
    )
    setDecisions((d) => {
      const next = { ...d }
      if (ok) next[proposal.id] = { kind: 'decided', decision }
      else delete next[proposal.id]
      return next
    })
  }

  const markAccepted = useCallback((op: OperationView) => {
    setDecisions((d) => (d[op.proposal_id]?.kind === 'decided' ? d : { ...d, [op.proposal_id]: { kind: 'decided', decision: 'ACCEPT' } }))
  }, [])

  const live = callState.phase === 'live'
  const voiceOffer = live && callState.proposal && callState.proposal.status !== 'text-only' ? callState.proposal : null
  const textOffer = !voiceOffer ? conversation.pending_proposal : null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={pageRef} className="relative min-h-0 flex-1 overflow-y-auto lg:flex lg:overflow-hidden">
        {!stageInView && (live || callState.phase === 'connecting' || callState.phase === 'requesting') && (
          <CallBar call={call} state={callState} />
        )}
        <aside ref={stageRef} className="flex flex-col border-b bg-sidebar lg:w-96 lg:shrink-0 lg:overflow-y-auto lg:border-r lg:border-b-0">
          <CallStage call={call} state={callState} />
          {API_MODE === 'mock' && live && (
            <Suspense fallback={null}>
              <MockCaller />
            </Suspense>
          )}
        </aside>

        <main className="min-w-0 lg:flex lg:flex-1 lg:flex-col">
          <div ref={threadRef} className="relative lg:flex-1 lg:overflow-y-auto" aria-live="polite">
            <div className="mx-auto flex w-full max-w-2xl flex-col gap-3 px-4 py-6">
              {thread.map((item) => (
                <ThreadEntry key={item.id} item={item} onSettled={() => void refresh()} onOperation={markAccepted} />
              ))}
              {thinking && <Typing />}
              {voiceOffer && (
                <div className="max-w-xl animate-bubble-in sm:ml-9">
                  <VoiceOfferCard offer={voiceOffer} />
                </div>
              )}
              {textOffer && (
                <div className="max-w-xl sm:ml-9">
                  <ConfirmationCard
                    proposal={textOffer}
                    state={decisions[textOffer.id] ?? { kind: 'open' }}
                    onDecide={(d) => void decide(textOffer, d)}
                  />
                </div>
              )}
              {sending && <Typing />}
            </div>
          </div>
        </main>
      </div>

      <form
        className="border-t bg-background px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault()
          submitText()
        }}
      >
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-1.5">
          <label htmlFor="voice-text" className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Keyboard aria-hidden className="size-3.5" /> {t('voice.text.title')}
          </label>
          <div className="flex items-end gap-2">
            <Textarea
              id="voice-text"
              rows={1}
              value={draft}
              onChange={(e) => setDraft(e.target.value.slice(0, MAX_TEXT))}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  submitText()
                }
              }}
              placeholder={t('voice.text.placeholder')}
              className="max-h-32 min-h-10 resize-none"
            />
            <Button type="submit" size="icon-lg" disabled={!draft.trim() || sending} aria-label={t('voice.text.send')}>
              <SendHorizontal aria-hidden />
            </Button>
          </div>
          {sendError != null && (
            <p role="alert" className="text-xs text-destructive">
              {t('chat.notSent', { reason: describeError(sendError, t) })}
            </p>
          )}
        </div>
      </form>
    </div>
  )
}

function ThreadEntry({
  item,
  onSettled,
  onOperation,
}: {
  item: ThreadItem
  onSettled: () => void
  onOperation: (op: OperationView) => void
}) {
  const { t } = useI18n()
  const result = item.result
  const extras = !!result && (result.cards.length > 0 || result.citations.length > 0 || result.operation_ids.length > 0)
  return (
    <div className="flex flex-col gap-2">
      <Bubble speaker={item.speaker} time={item.time} spoken={item.spoken} pending={item.pending} animate={item.animate}>
        {item.text}
      </Bubble>
      {item.fallback && <p className="text-[11px] text-muted-foreground sm:ml-9">{t('voice.fallback')}</p>}
      {result && extras && (
        <div className="flex max-w-xl flex-col gap-2 sm:ml-9">
          {result.cards
            .filter((card) => card.type !== 'confirmation') // the live offer is shown once, below the thread
            .map((card, i) => (
              <div
                key={i}
                className={item.animate ? 'animate-bubble-in' : undefined}
                style={item.animate ? { animationDelay: `${180 + i * 110}ms` } : undefined}
              >
                <ChatCard card={card} renderConfirmation={() => null} />
              </div>
            ))}
          {result.operation_ids.map((id) => (
            <OperationTracker key={id} operationId={id} onUpdate={onOperation} onSettled={onSettled} />
          ))}
          <CitationList citations={result.citations} />
        </div>
      )}
    </div>
  )
}

const OFFER_STATUS: Record<
  Exclude<ProposalStatus, 'text-only'>,
  { icon: React.ReactNode; key: 'voice.proposal.reading' | 'voice.proposal.listening' | 'voice.proposal.interrupted' }
> = {
  reading: { icon: <Volume2 />, key: 'voice.proposal.reading' },
  awaiting: { icon: <Mic />, key: 'voice.proposal.listening' },
  interrupted: { icon: <Volume2 />, key: 'voice.proposal.interrupted' },
}

/** The offer Resolve made during the call. It is answered by voice; nothing here can accept it. */
function VoiceOfferCard({ offer }: { offer: NonNullable<CallState['proposal']> }) {
  const { t } = useI18n()
  const p = offer.data
  const status = OFFER_STATUS[offer.status as keyof typeof OFFER_STATUS]
  const left = useCountdown(p.expires_at)
  return (
    <CardFrame
      icon={<ShieldCheck />}
      title={t('voice.proposal.title')}
      tone="warning"
      aside={<StatusBadge tone="warning">{t('confirm.waiting')}</StatusBadge>}
    >
      <dl className="flex flex-col gap-2">
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.action')}</dt>
          <dd className="font-medium">{hasMessage(`action.${p.action_type}`) ? t(`action.${p.action_type}`) : humanize(p.action_type)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.appliesTo')}</dt>
          <dd>{p.target_label}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">{t('confirm.means')}</dt>
          <dd>{p.consequences}</dd>
        </div>
      </dl>
      <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Timer aria-hidden className="size-3.5" />
        {t('confirm.validUntil', { time: formatTime(p.expires_at), left })}
      </p>
      <p
        aria-live="polite"
        className={cn(
          'mt-3 flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium [&_svg]:size-4 [&_svg]:shrink-0',
          offer.status === 'awaiting' ? 'bg-primary text-primary-foreground' : 'bg-card',
        )}
      >
        <span aria-hidden className={cn(offer.status !== 'interrupted' && 'animate-pulse')}>
          {status.icon}
        </span>
        {t(status.key)}
      </p>
    </CardFrame>
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
