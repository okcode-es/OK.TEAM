import { useEffect, useState, type FormEvent } from 'react'
import { CheckCircle2, Eye, EyeOff, Loader2, X, Zap } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/features/auth/AuthContext'
import { LoginFluidBackdrop } from '@/features/auth/LoginFluidBackdrop'
import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
import { getSupabaseAuth } from '@/lib/supabase'
import { cn } from '@/lib/utils'

const DEV_PRESETS = [
  { labelKey: 'auth.devPresets.owner', email: 'owner@dragon.es' },
  { labelKey: 'auth.devPresets.hr', email: 'hr@dragon.es' },
  { labelKey: 'auth.devPresets.manager', email: 'manager@dragon.es' },
  { labelKey: 'auth.devPresets.employee', email: 'zhou@dragon.es' },
  { labelKey: 'auth.devPresets.accountant', email: 'gestoria@asesoria.es' },
] as const

function BrandMark() {
  return (
    <div className="flex items-center gap-2.5">
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#2563EB] shadow-xs"
        aria-hidden="true"
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <rect
            x="3.5"
            y="3.5"
            width="7.5"
            height="7.5"
            rx="2"
            fill="white"
          />
          <rect
            x="13"
            y="3.5"
            width="7.5"
            height="7.5"
            rx="2"
            fill="white"
            fillOpacity="0.65"
          />
          <rect
            x="3.5"
            y="13"
            width="7.5"
            height="7.5"
            rx="2"
            fill="white"
            fillOpacity="0.65"
          />
          <path
            d="M14.2 16.8L16.2 18.8L20 14.5"
            stroke="white"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span
        translate="no"
        className="text-base font-semibold tracking-tight"
      >
        okTeam HR
      </span>
    </div>
  )
}

export function LoginPage() {
  const { t, i18n } = useTranslation()
  const { signInWithSupabase, signInWithDevEmail, devAuthAllowed } = useAuth()
  const hasSupabase = getSupabaseAuth() !== null
  const showDevDrawer = import.meta.env.DEV || devAuthAllowed

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [showForgotHelp, setShowForgotHelp] = useState(false)
  const [devOpen, setDevOpen] = useState(false)
  const [devEmail, setDevEmail] = useState('owner@dragon.es')
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const activeLocale = (i18n.language as SupportedLocale) || 'zh-CN'

  useEffect(() => {
    if (!devOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setDevOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [devOpen])

  const handleSupabaseSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (submitting) return
    setErrorMessage(null)

    if (!hasSupabase) {
      setErrorMessage(t('auth.supabaseNotConfigured'))
      return
    }

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
    if (submitting) return
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

  const renderLocaleSwitcher = () => (
    <div
      className="inline-flex items-center rounded-lg border border-slate-200 bg-slate-50 p-0.5"
      role="group"
      aria-label={t('locale.label')}
    >
      {SUPPORTED_LOCALES.map((locale) => {
        const isActive = activeLocale === locale
        return (
          <button
            key={locale}
            type="button"
            aria-pressed={isActive}
            onClick={() => void i18n.changeLanguage(locale)}
            className={cn(
              'rounded-md px-2.5 py-1 text-xs font-medium transition-all duration-150 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
              isActive
                ? 'bg-white text-slate-900 shadow-2xs'
                : 'text-slate-500 hover:text-slate-900',
            )}
          >
            {t(`auth.localeShort.${locale}`)}
          </button>
        )
      })}
    </div>
  )

  return (
    <div className="flex min-h-screen flex-col bg-white text-slate-900 lg:flex-row">
      {/* Desktop Left Brand Panel (45%, Deep Navy #0B1120) */}
      <aside className="relative hidden flex-col justify-between overflow-hidden bg-[#0B1120] p-10 text-white lg:flex lg:w-[45%] xl:p-12">
        <LoginFluidBackdrop />

        <div className="relative z-10">
          <BrandMark />
        </div>

        <div className="relative z-10 my-auto max-w-md space-y-8 py-12">
          <div className="space-y-3">
            <h1 className="text-2xl leading-snug font-semibold tracking-tight text-white xl:text-[28px]">
              {t('auth.brandHeadline')}
            </h1>
            <p className="text-sm leading-relaxed text-slate-300">
              {t('auth.brandSubtitle')}
            </p>
          </div>

          <ul className="space-y-4">
            {(
              [
                'auth.trustBullets.rdl',
                'auth.trustBullets.gdpr',
                'auth.trustBullets.bilingual',
              ] as const
            ).map((key) => (
              <li key={key} className="flex items-start gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-blue-500/15 text-blue-400">
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                </span>
                <span className="text-sm leading-relaxed text-slate-200">
                  {t(key)}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="relative z-10 text-xs text-slate-400">
          {t('auth.euResidency')}
        </div>
      </aside>

      {/* Right Login Area (55%, Pure White) */}
      <div className="flex flex-1 flex-col bg-white lg:w-[55%]">
        {/* Mobile Top Bar (< lg) */}
        <header className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5 lg:hidden">
          <div className="text-slate-900">
            <BrandMark />
          </div>
          {renderLocaleSwitcher()}
        </header>

        {/* Desktop Top-Right Language Switcher */}
        <div className="hidden justify-end px-8 pt-6 lg:flex">
          {renderLocaleSwitcher()}
        </div>

        {/* Vertically Centered Form Container (No outer card border) */}
        <main className="flex flex-1 items-center justify-center px-6 py-10 sm:px-10">
          <div className="mx-auto w-full max-w-[400px]">
            <div
              className="okteam-fade-up mb-7 space-y-1.5"
              style={{ animationDelay: '0ms' }}
            >
              <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
                {t('auth.title')}
              </h2>
              <p className="text-sm text-slate-500">{t('auth.subtitle')}</p>
            </div>

            {errorMessage && (
              <div
                role="alert"
                className="mb-5 rounded-lg border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs leading-relaxed text-rose-700"
              >
                {errorMessage}
              </div>
            )}

            <form onSubmit={handleSupabaseSubmit} className="space-y-5">
              <div
                className="okteam-fade-up space-y-5"
                style={{ animationDelay: '60ms' }}
              >
                <div className="space-y-1.5">
                  <label
                    htmlFor="email"
                    className="block text-sm font-medium text-slate-700"
                  >
                    {t('auth.email')}
                  </label>
                  <input
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
                    className="h-11 w-full rounded-lg border border-slate-300 bg-white px-3.5 text-sm text-slate-900 transition-[border-color,box-shadow] duration-150 placeholder:text-slate-400 focus:border-[#2563EB] focus:ring-4 focus:ring-[#2563EB]/10 focus:outline-none"
                  />
                </div>

                <div className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <label
                      htmlFor="current-password"
                      className="block text-sm font-medium text-slate-700"
                    >
                      {t('auth.password')}
                    </label>
                    <button
                      type="button"
                      onClick={() => setShowForgotHelp((prev) => !prev)}
                      className="text-sm font-medium text-[#2563EB] hover:underline focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                    >
                      {t('auth.forgotPassword')}
                    </button>
                  </div>
                  <div className="relative">
                    <input
                      id="current-password"
                      name="password"
                      type={showPassword ? 'text' : 'password'}
                      autoComplete="current-password"
                      enterKeyHint="done"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="h-11 w-full rounded-lg border border-slate-300 bg-white pr-11 pl-3.5 text-sm text-slate-900 transition-[border-color,box-shadow] duration-150 placeholder:text-slate-400 focus:border-[#2563EB] focus:ring-4 focus:ring-[#2563EB]/10 focus:outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((prev) => !prev)}
                      aria-label={
                        showPassword
                          ? t('auth.hidePassword')
                          : t('auth.showPassword')
                      }
                      className="absolute top-1/2 right-2.5 -translate-y-1/2 rounded-md p-1.5 text-slate-400 transition-colors hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                    >
                      {showPassword ? (
                        <EyeOff className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Eye className="h-4 w-4" aria-hidden="true" />
                      )}
                    </button>
                  </div>

                  {showForgotHelp && (
                    <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">
                      {t('auth.forgotPasswordHelp')}
                    </p>
                  )}
                </div>
              </div>

              <div
                className="okteam-fade-up"
                style={{ animationDelay: '120ms' }}
              >
                <button
                  type="submit"
                  disabled={submitting}
                  className="flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-[#2563EB] px-4 text-sm font-medium text-white shadow-xs transition-all duration-150 hover:bg-blue-700 focus-visible:ring-4 focus-visible:ring-[#2563EB]/25 focus-visible:outline-none active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {submitting && (
                    <Loader2
                      className="h-4 w-4 animate-spin"
                      aria-hidden="true"
                    />
                  )}
                  <span>
                    {submitting ? t('auth.signingIn') : t('auth.signIn')}
                  </span>
                </button>
              </div>
            </form>

            <p className="mt-6 text-center text-xs leading-relaxed text-slate-600">
              {t('auth.termsNotice')}
            </p>
          </div>
        </main>
      </div>

      {/* Floating Dev Login Trigger & Popover (Dev / Demo Only) */}
      {showDevDrawer && (
        <>
          {devOpen && (
            <div
              role="dialog"
              aria-label={t('auth.devSectionTitle')}
              className="fixed right-4 bottom-14 z-50 w-80 rounded-xl border border-slate-200 bg-white p-4 shadow-lg"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold tracking-wide text-slate-900">
                  {t('auth.devSectionTitle')}
                </h3>
                <button
                  type="button"
                  onClick={() => setDevOpen(false)}
                  aria-label={t('common.close')}
                  className="rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
              <p className="mb-3 text-[11px] leading-relaxed text-slate-500">
                {t('auth.devHint')}
              </p>

              <div className="mb-3 flex flex-wrap gap-1.5">
                {DEV_PRESETS.map((preset) => (
                  <button
                    key={preset.email}
                    type="button"
                    disabled={submitting}
                    onClick={() => {
                      setDevEmail(preset.email)
                      void handleDevSubmit(preset.email)
                    }}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-100 disabled:opacity-50"
                  >
                    {t(preset.labelKey)}
                  </button>
                ))}
              </div>

              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  void handleDevSubmit(devEmail)
                }}
                className="flex gap-2"
              >
                <input
                  type="email"
                  name="devEmail"
                  spellCheck={false}
                  required
                  value={devEmail}
                  onChange={(e) => setDevEmail(e.target.value)}
                  placeholder={t('auth.devEmailPlaceholder')}
                  className="h-9 min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-2.5 text-xs text-slate-900 placeholder:text-slate-400 focus:border-[#2563EB] focus:ring-2 focus:ring-[#2563EB]/15 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={submitting}
                  className="h-9 shrink-0 rounded-lg bg-slate-900 px-3 text-xs font-medium text-white transition-colors hover:bg-slate-800 disabled:opacity-50"
                >
                  {t('auth.devSignIn')}
                </button>
              </form>
            </div>
          )}

          <button
            type="button"
            onClick={() => setDevOpen((prev) => !prev)}
            aria-expanded={devOpen}
            className="fixed right-4 bottom-4 z-40 inline-flex items-center gap-1.5 rounded-lg border border-slate-200/80 bg-white/80 px-3 py-1.5 text-xs font-medium text-slate-600 shadow-xs backdrop-blur-xs transition-colors hover:bg-white hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
          >
            <Zap className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
            <span>{t('auth.devTrigger')}</span>
          </button>
        </>
      )}
    </div>
  )
}
