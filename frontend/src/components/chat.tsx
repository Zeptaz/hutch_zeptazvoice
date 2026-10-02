// Bubble and avatar from the Resolve text chat (hutch_resolve frontend/src/routes/customer/ChatShell.tsx),
// so the call's captions read like the chat.
import { Bot, Mic } from 'lucide-react'
import { useI18n } from '@/i18n/context'
import { formatTime } from '@/lib/format'
import { cn } from '@/lib/utils'

export function Bubble({
  speaker,
  time,
  animate,
  pending,
  spoken,
  children,
}: {
  speaker: 'USER' | 'ASSISTANT'
  time?: string
  /** Rise in from the speaker's corner (new messages only). */
  animate?: boolean
  /** The customer's message while it is still being sent. */
  pending?: boolean
  /** Said during a voice call rather than typed. */
  spoken?: boolean
  children: React.ReactNode
}) {
  const { t } = useI18n()
  const mine = speaker === 'USER'
  return (
    <div
      className={cn(
        'flex max-w-[85%] gap-2',
        mine ? 'ml-auto origin-bottom-right flex-row-reverse' : 'mr-auto origin-bottom-left',
        animate && 'animate-bubble-in',
      )}
    >
      {!mine && <BotAvatar />}
      <div className={cn('flex flex-col gap-1', mine && 'items-end')}>
        <span className="sr-only">{mine ? t('chat.youSaid') : t('chat.assistantSaid')}</span>
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap transition-opacity',
            // Captions sit on the grey call panel, so Resolve's bubble is white here (grey in the chat).
            mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-card',
            pending && 'opacity-80',
          )}
        >
          {children}
        </div>
        {(pending || time || spoken) && (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            {spoken && <Mic aria-hidden className="size-3" />}
            {pending ? t('chat.sending') : time && <time>{formatTime(time)}</time>}
          </span>
        )}
      </div>
    </div>
  )
}

export function BotAvatar({ thinking }: { thinking?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        'mt-1 grid size-7 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground',
        thinking && 'animate-thinking-ring',
      )}
    >
      <Bot className="size-4" />
    </span>
  )
}
