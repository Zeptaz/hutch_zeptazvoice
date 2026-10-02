import { useState } from 'react'
import { MessageCircleWarning, Zap } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n/context'
import { mockVoice } from '@/voice/mockSocket'

const LINES = [
  'I recharged LKR 1000 this morning but my balance is only LKR 420',
  'Yes, go ahead',
  'No, leave it',
  'Thanks, that is all',
]

/** Mock mode only: stands in for the caller's voice so every call state can be reached without a microphone. */
export default function MockCaller() {
  const { t } = useI18n()
  const [failCheck, setFailCheck] = useState(mockVoice.failSpeechCheck)
  return (
    <section aria-label={t('voice.mock.title')} className="mx-4 mb-4 flex flex-col gap-2 rounded-2xl border border-dashed border-foreground/20 p-3 text-left">
      <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('voice.mock.title')}</h2>
      <p className="text-xs text-muted-foreground">{t('voice.mock.note')}</p>
      <div className="flex flex-wrap gap-1.5">
        {LINES.map((line) => (
          <Button key={line} variant="outline" size="sm" className="h-auto max-w-full rounded-full py-1 text-left whitespace-normal" onClick={() => mockVoice.say(line)}>
            “{line}”
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button variant="ghost" size="sm" onClick={() => mockVoice.interrupt()}>
          <Zap aria-hidden /> Talk over the reply
        </Button>
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={failCheck}
          onClick={() => {
            mockVoice.failSpeechCheck = !failCheck
            setFailCheck(!failCheck)
          }}
        >
          <MessageCircleWarning aria-hidden /> {failCheck ? 'Speech check fails' : 'Speech check passes'}
        </Button>
      </div>
    </section>
  )
}
