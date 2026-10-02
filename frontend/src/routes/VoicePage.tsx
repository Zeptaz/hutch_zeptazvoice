import { useState, type ReactNode } from 'react'
import { LogOut } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import { BrandMark } from '@/components/BrandMark'
import { LanguageToggle } from '@/components/LanguageToggle'
import { SimulationBanner } from '@/components/SimulationBanner'
import { ErrorState, LoadingState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useI18n } from '@/i18n/context'
import { LanguageProvider } from '@/i18n/LanguageProvider'
import { useCustomerSession } from '@/session/context'
import { CustomerSessionProvider } from '@/session/providers'
import { VoiceShell } from './VoiceShell'

/**
 * A standalone call page, separate from the Resolve text chat: one headline, one call panel.
 * Dark by design so the panel's glow carries the call state.
 */
export default function VoicePage() {
  return (
    <LanguageProvider>
      <CustomerSessionProvider>
        <div className="voice-backdrop flex min-h-dvh flex-col">
          <SimulationBanner />
          <Page />
        </div>
      </CustomerSessionProvider>
    </LanguageProvider>
  )
}

function Page() {
  const { status, session, error, establish, logout, retry } = useCustomerSession()
  const { language, setLanguage, t } = useI18n()
  const signedIn = status === 'active' && session?.role === 'CUSTOMER'

  // "Talk to {name}" with the product name in the accent colour, in any word order.
  const [before, after] = t('voice.hero', { name: '\u0000' }).split('\u0000')

  return (
    <>
      <header className="sticky top-[env(safe-area-inset-top,0px)] z-20 px-4 pt-4">
        <nav className="mx-auto flex max-w-3xl items-center justify-between gap-3 rounded-full border border-white/10 bg-card/70 py-2.5 pr-2.5 pl-6 shadow-lg shadow-black/30 backdrop-blur">
          <BrandMark />
          <div className="flex items-center gap-1.5">
            <LanguageToggle value={language} onChange={setLanguage} label={t('lang.label')} />
            {signedIn && (
              <Button variant="ghost" size="icon-sm" className="rounded-full" onClick={() => void logout()} aria-label={t('voice.signOut')} title={t('voice.signOut')}>
                <LogOut aria-hidden />
              </Button>
            )}
          </div>
        </nav>
      </header>

      <main className="flex flex-1 flex-col items-center px-4 pt-12 pb-10">
        <div className="flex max-w-xl flex-col items-center gap-4 text-center">
          <h1 className="text-4xl font-bold tracking-tight text-balance sm:text-5xl">
            {before}
            <span className="text-primary">Resolve</span>
            {after}
          </h1>
          <p className="max-w-md text-base leading-relaxed text-balance text-muted-foreground">{t('voice.heroBody')}</p>
        </div>

        <div className="mt-10 flex w-full max-w-md flex-col gap-8">
          {signedIn ? (
            <VoiceShell session={session} />
          ) : (
            <Panel>
              {status === 'restoring' ? (
                <LoadingState label={t('voice.restoring')} rows={2} />
              ) : status === 'error' ? (
                <ErrorState title={t('voice.openFailed')} error={error} onRetry={retry} />
              ) : (
                <SignIn expired={status === 'expired'} onSignIn={(body) => establish(() => customerApi.login(body))} />
              )}
            </Panel>
          )}
        </div>
        <p className="mt-8 text-xs text-muted-foreground">{t('voice.privacy')}</p>
      </main>
    </>
  )
}

/** The rounded call card with an orange glow. `glow` is a CSS opacity, 0..1. */
export function Panel({ children, glowRef }: { children: ReactNode; glowRef?: React.Ref<HTMLDivElement> }) {
  return (
    <section className="relative isolate overflow-hidden rounded-[2rem] border border-white/10 bg-card px-6 py-10 shadow-2xl shadow-black/40">
      <div ref={glowRef} aria-hidden className="voice-glow pointer-events-none absolute inset-0 -z-10" />
      {children}
    </section>
  )
}

function SignIn({ expired, onSignIn }: { expired: boolean; onSignIn: (body: { demo_identity: string; credential: string }) => Promise<void> }) {
  const { t } = useI18n()
  const [identity, setIdentity] = useState('')
  const [credential, setCredential] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await onSignIn({ demo_identity: identity.trim(), credential })
    } catch (err) {
      setError(err)
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5 text-center">
        <h2 className="text-lg font-semibold text-balance">{t('voice.signInTitle')}</h2>
        <p className="text-sm text-balance text-muted-foreground">{expired ? t('voice.expired') : t('voice.signInBody')}</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="identity">{t('voice.identity')}</Label>
        <Input id="identity" autoComplete="username" required value={identity} onChange={(e) => setIdentity(e.target.value)} className="h-10 rounded-xl" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="credential">{t('voice.credential')}</Label>
        <Input
          id="credential"
          type="password"
          autoComplete="current-password"
          required
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          className="h-10 rounded-xl"
        />
      </div>
      {error != null && (
        <p role="alert" className="text-sm text-destructive">
          {describeError(error, t)}
        </p>
      )}
      <Button type="submit" size="lg" className="h-11 rounded-full" disabled={busy || !identity.trim() || !credential}>
        {busy ? t('voice.signingIn') : t('voice.signIn')}
      </Button>
    </form>
  )
}
