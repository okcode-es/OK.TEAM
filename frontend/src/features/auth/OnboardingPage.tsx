import { useState, type FormEvent } from 'react'
import { Building2, LogOut } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import { Badge } from '@/components/ui/badge'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAuth } from '@/features/auth/AuthContext'
import { SPAIN_TIMEZONES } from '@/lib/ime'

export function OnboardingPage() {
  const { t, i18n } = useTranslation()
  const { profile, refreshProfile, signOut } = useAuth()

  const [legalName, setLegalName] = useState('')
  const [tradeName, setTradeName] = useState('')
  const [cifNif, setCifNif] = useState('')
  const [ownerDisplayName, setOwnerDisplayName] = useState('')
  const [defaultTimezone, setDefaultTimezone] = useState<string>('Europe/Madrid')
  const [defaultLocale, setDefaultLocale] = useState<'zh-CN' | 'es-ES'>(
    i18n.language === 'es-ES' ? 'es-ES' : 'zh-CN',
  )
  const [submitting, setSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setErrorMessage(null)
    setSubmitting(true)
    try {
      const { data, error } = await apiClient.POST('/api/v1/tenants', {
        body: {
          legal_name: legalName,
          trade_name: tradeName,
          cif_nif: cifNif,
          default_timezone: defaultTimezone,
          default_locale: defaultLocale,
          owner_display_name: ownerDisplayName,
        },
      })
      if (error || !data) {
        setErrorMessage(extractApiError(error, t('common.error')))
        return
      }
      await refreshProfile(data.id)
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10 text-slate-900">
      <div className="mx-auto max-w-xl space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full bg-teal-700"
              aria-hidden="true"
            />
            <span className="text-sm font-semibold text-slate-900">
              {t('brand')}
            </span>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void signOut()}
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            {t('nav.signOut')}
          </Button>
        </div>

        <Card className="border-slate-200 bg-white shadow-none">
          <CardHeader className="space-y-2">
            <Badge
              variant="outline"
              className="w-fit border-slate-200 bg-slate-50 text-slate-600"
            >
              <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
              {t('onboarding.badge')}
            </Badge>
            <CardTitle className="text-lg font-semibold text-slate-900">
              {t('onboarding.title')}
            </CardTitle>
            <CardDescription className="text-sm text-slate-600">
              {t('onboarding.subtitle')}
              {profile?.email ? ` (${profile.email})` : ''}
            </CardDescription>
          </CardHeader>

          <CardContent>
            {errorMessage && (
              <div
                role="alert"
                className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {errorMessage}
              </div>
            )}

            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="legal_name">{t('onboarding.legalName')}</Label>
                  <Input
                    id="legal_name"
                    name="legal_name"
                    required
                    value={legalName}
                    onChange={(e) => setLegalName(e.target.value)}
                    placeholder={t('onboarding.legalNamePlaceholder')}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="trade_name">{t('onboarding.tradeName')}</Label>
                  <Input
                    id="trade_name"
                    name="trade_name"
                    required
                    value={tradeName}
                    onChange={(e) => setTradeName(e.target.value)}
                    placeholder={t('onboarding.tradeNamePlaceholder')}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="cif_nif">{t('onboarding.cifNif')}</Label>
                  <Input
                    id="cif_nif"
                    name="cif_nif"
                    required
                    spellCheck={false}
                    className="uppercase tabular-nums"
                    value={cifNif}
                    onChange={(e) => setCifNif(e.target.value)}
                    placeholder={t('onboarding.cifNifPlaceholder')}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="owner_display_name">
                    {t('onboarding.ownerDisplayName')}
                  </Label>
                  <Input
                    id="owner_display_name"
                    name="owner_display_name"
                    autoComplete="name"
                    value={ownerDisplayName}
                    onChange={(e) => setOwnerDisplayName(e.target.value)}
                    placeholder={t('onboarding.ownerDisplayNamePlaceholder')}
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="default_timezone">
                    {t('onboarding.defaultTimezone')}
                  </Label>
                  <Select
                    value={defaultTimezone}
                    onValueChange={setDefaultTimezone}
                  >
                    <SelectTrigger id="default_timezone" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {SPAIN_TIMEZONES.map((tz) => (
                        <SelectItem key={tz} value={tz}>
                          {tz}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="default_locale">
                    {t('onboarding.defaultLocale')}
                  </Label>
                  <Select
                    value={defaultLocale}
                    onValueChange={(value) =>
                      setDefaultLocale(value as 'zh-CN' | 'es-ES')
                    }
                  >
                    <SelectTrigger id="default_locale" className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="zh-CN">中文 (zh-CN)</SelectItem>
                      <SelectItem value="es-ES">Español (es-ES)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="pt-2">
                <Button type="submit" disabled={submitting}>
                  {submitting
                    ? t('onboarding.submitting')
                    : t('onboarding.submit')}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
