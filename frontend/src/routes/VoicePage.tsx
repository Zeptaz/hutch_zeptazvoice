import { useState } from 'react'
import { LogOut } from 'lucide-react'
import { customerApi } from '@/api/endpoints'
import { describeError } from '@/api/errors'
import { BrandMark } from '@/components/BrandMark'
import { LanguageToggle } from '@/components/chat'
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

export default function VoicePage() {
  return (
    <LanguageProvider>
      <CustomerSessionProvider>
        <div className="flex h-dvh flex-col">
          <SimulationBanner />
          <SessionGate />
        </div>
      </CustomerSessionProvider>
    </LanguageProvider>
  )
}

export function VoiceHeader({ onSignOut }: { onSignOut?: () => void }) {
  const { language, setLanguage, t } = useI18n()
  return (
    <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
      <BrandMark subtitle={t('brand.voiceSubtitle')} />
      <div className="flex items-center gap-2">
        <LanguageToggle value={language} onChange={setLanguage} label={t('lang.label')} />
        {onSignOut && (
          <Button variant="ghost" size="icon-sm" onClick={onSignOut} aria-label={t('voice.signOut')} title={t('voice.signOut')}>
            <LogOut aria-hidden />
          </Button>
        )}
      </div>
    </header>
  )
}

/** A voice call investigates the caller's own line, so it needs a CUSTOMER (demo line) session. */
function SessionGate() {
  const { status, session, error, establish, logout, retry } = useCustomerSession()
  const { t } = useI18n()

  if (status === 'active' && session?.role === 'CUSTOMER') return <VoiceShell session={session} onSignOut={() => void logout()} />

  return (
    <div className="flex flex-1 flex-col overflow-y-auto">
      <VoiceHeader />
      <main className="mx-auto w-full max-w-sm flex-1 px-4 pt-10 pb-8">
        {status === 'restoring' ? (
          <LoadingState label={t('voice.restoring')} rows={2} />
        ) : status === 'error' ? (
          <ErrorState title={t('voice.openFailed')} error={error} onRetry={retry} />
        ) : (
          <SignIn expired={status === 'expired'} onSignIn={(body) => establish(() => customerApi.login(body))} />
        )}
      </main>
    </div>
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
    <form onSubmit={submit} className="flex flex-col gap-4 rounded-2xl bg-muted/70 p-5">
      <div className="flex flex-col gap-1">
        <h1 className="text-lg font-semibold text-balance">{t('voice.signInTitle')}</h1>
        <p className="text-sm text-muted-foreground">{expired ? t('voice.expired') : t('voice.signInBody')}</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="identity">{t('voice.identity')}</Label>
        <Input id="identity" autoComplete="username" required value={identity} onChange={(e) => setIdentity(e.target.value)} className="bg-card" />
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
          className="bg-card"
        />
      </div>
      {error != null && (
        <p role="alert" className="text-sm text-destructive">
          {describeError(error, t)}
        </p>
      )}
      <Button type="submit" disabled={busy || !identity.trim() || !credential}>
        {busy ? t('voice.signingIn') : t('voice.signIn')}
      </Button>
    </form>
  )
}
