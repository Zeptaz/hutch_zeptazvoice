// Chat pieces shared with the Resolve text chat (hutch_resolve frontend/src/routes/customer/ChatShell.tsx),
// so a voice call reads the same as a typed conversation.
import { useEffect, useState } from 'react'
import { Bot, Mic } from 'lucide-react'
import type { Language } from '@/api/types'
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
            mine ? 'rounded-br-md bg-primary text-primary-foreground' : 'rounded-bl-md bg-muted',
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

const STILL_WORKING_MS = 3500

/** Resolve is working on a reply. After a few seconds a short note says it hasn't stalled. */
export function Typing() {
  const { t } = useI18n()
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), STILL_WORKING_MS)
    return () => window.clearTimeout(id)
  }, [])
  return (
    <div role="status" className="mr-auto flex origin-bottom-left animate-bubble-in gap-2">
      <BotAvatar thinking />
      <div className="flex flex-col gap-1">
        <span className="sr-only">{t('chat.typing')}</span>
        <div className="flex h-10 items-center gap-1.5 rounded-2xl rounded-bl-md bg-muted px-4">
          {[0, 160, 320].map((d) => (
            <span key={d} className="size-2 animate-typing-dot rounded-full bg-muted-foreground" style={{ animationDelay: `${d}ms` }} />
          ))}
        </div>
        {slow && <span className="animate-bubble-in text-[11px] text-muted-foreground">{t('chat.stillWorking')}</span>}
      </div>
    </div>
  )
}

// Each language is named in its own script so it is recognisable whatever the current UI language.
const LANGUAGES: { value: Language; short: string; label: string }[] = [
  { value: 'en', short: 'EN', label: 'English' },
  { value: 'si', short: 'සි', label: 'සිංහල' },
  { value: 'ta', short: 'த', label: 'தமிழ்' },
]

/** Segmented EN | සි | த switch. A radio group: arrow keys move between languages, Tab leaves the group. */
export function LanguageToggle({ value, onChange, label }: { value: Language; onChange: (l: Language) => void; label: string }) {
  const move = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    e.preventDefault()
    const i = LANGUAGES.findIndex((l) => l.value === value)
    const next = LANGUAGES[(i + step + LANGUAGES.length) % LANGUAGES.length]
    onChange(next.value)
    e.currentTarget.querySelector<HTMLButtonElement>(`[data-lang="${next.value}"]`)?.focus()
  }
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={move} className="flex h-7 items-center rounded-full bg-muted p-0.5">
      {LANGUAGES.map((l) => {
        const selected = l.value === value
        return (
          <button
            key={l.value}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={l.label}
            title={l.label}
            lang={l.value}
            data-lang={l.value}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(l.value)}
            className={cn(
              'h-6 min-w-8 rounded-full px-2 text-xs font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60',
              selected ? 'bg-foreground text-background' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {l.short}
          </button>
        )
      })}
    </div>
  )
}
