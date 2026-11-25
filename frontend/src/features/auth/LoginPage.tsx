import { useState, type FormEvent } from 'react'
import { Eye, EyeOff, Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/features/auth/AuthContext'
import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
import { getSupabaseAuth } from '@/lib/supabase'

const DEV_PRESETS = [
  { label: 'Owner', email: 'owner@dragon.es' },
  { label: 'HR', email: 'hr@dragon.es' },
  { label: 'Store Mgr', email: 'manager@dragon.es' },
  { label: 'Gestoría', email: 'gestoria@asesoria.es' },
] as const

export function LoginPage() {
  const { t, i18n } = useTranslation()
  const { signInWithSupabase, signInWithDevEmail, devAuthAllowed } = useAuth()
  const hasSupabase = getSupabaseAuth() !== null

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [devEmail, setDevEmail] = useState('owner@dragon.es')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const activeLocale = (i18n.language as SupportedLocale) || 'zh-CN'

  const handleSupabaseSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setErrorMessage(null)
    setSubmitting(true)
    try {
      await signInWithSupabase(email, password)
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : t('auth.errorGeneric'),
      )
    } finally {
      setSubmitting(false)
    }
  }

  const handleDevSubmit = async (targetEmail: string) => {
    setErrorMessage(null)
    setSubmitting(true)
    try {
      await signInWithDevEmail(targetEmail)
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : t('auth.errorGeneric'),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full bg-teal-700"
              aria-hidden="true"
            />
            <span className="text-sm font-semibold tracking-tight text-slate-900">
              {t('brand')}
            </span>
          </div>

          <div
            className="flex items-center gap-1"
            role="group"
            aria-label={t('locale.label')}
          >
            <Globe className="mr-1 h-4 w-4 text-slate-400" aria-hidden="true" />
            {SUPPORTED_LOCALES.map((locale) => (
              <Button
                key={locale}
                type="button"
                size="sm"
                variant={activeLocale === locale ? 'default' : 'ghost'}
                aria-pressed={activeLocale === locale}
                onClick={() => void i18n.changeLanguage(locale)}
              >
                {t(`locale.${locale}`)}
              </Button>
            ))}
          </div>
        </div>
      </header>

      <main className="mx-auto flex max-w-md flex-col px-4 py-12">
        <Card className="border-slate-200 bg-white shadow-none">
          <CardHeader className="space-y-1">
            <CardTitle className="text-lg font-semibold text-slate-900">
              {t('auth.title')}
            </CardTitle>
            <CardDescription className="text-sm text-slate-600">
              {t('auth.subtitle')}
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-6">
            {errorMessage && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {errorMessage}
              </div>
            )}

            <form onSubmit={handleSupabaseSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="email">{t('auth.email')}</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  spellCheck={false}
                  enterKeyHint="next"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="owner@empresa.es"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="current-password">{t('auth.password')}</Label>
                <div className="relative">
                  <Input
                    id="current-password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    enterKeyHint="done"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((prev) => !prev)}
                    aria-label={
                      showPassword
                        ? t('auth.hidePassword')
                        : t('auth.showPassword')
                    }
                    className="absolute top-1/2 right-2.5 -translate-y-1/2 rounded p-1 text-slate-500 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-teal-700 focus-visible:outline-none"
                  >
                    {showPassword ? (
                      <EyeOff className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <Eye className="h-4 w-4" aria-hidden="true" />
                    )}
                  </button>
                </div>
              </div>

              {!hasSupabase && (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {t('auth.supabaseNotConfigured')}
                </p>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={submitting || !hasSupabase}
              >
                {submitting ? t('auth.signingIn') : t('auth.signIn')}
              </Button>
            </form>

            {devAuthAllowed && (
              <>
                <Separator />
                <div className="space-y-3">
                  <div className="space-y-1">
                    <h2 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                      {t('auth.devSectionTitle')}
                    </h2>
                    <p className="text-xs text-slate-500">{t('auth.devHint')}</p>
                  </div>

                  <div className="flex flex-wrap gap-1.5">
                    {DEV_PRESETS.map((preset) => (
                      <Button
                        key={preset.email}
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={submitting}
                        onClick={() => {
                          setDevEmail(preset.email)
                          void handleDevSubmit(preset.email)
                        }}
                      >
                        {preset.label}
                      </Button>
                    ))}
                  </div>

                  <form
                    onSubmit={(e) => {
                      e.preventDefault()
                      void handleDevSubmit(devEmail)
                    }}
                    className="flex flex-wrap gap-2"
                  >
                    <Input
                      type="email"
                      name="devEmail"
                      spellCheck={false}
                      required
                      value={devEmail}
                      onChange={(e) => setDevEmail(e.target.value)}
                      placeholder={t('auth.devEmailPlaceholder')}
                      className="min-w-48 flex-1"
                    />
                    <Button
                      type="submit"
                      variant="secondary"
                      disabled={submitting}
                    >
                      {t('auth.devSignIn')}
                    </Button>
                  </form>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
