import type { Language } from '@/api/types'
import { cn } from '@/lib/utils'

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
