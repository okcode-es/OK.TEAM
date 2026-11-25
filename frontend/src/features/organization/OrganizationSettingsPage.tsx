import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type { TenantResponse } from '@/api/types'
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
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { isImeComposing, SPAIN_TIMEZONES } from '@/lib/ime'

export function OrganizationSettingsPage() {
  const { t } = useTranslation()
  const { activeTenant, refreshProfile } = useAuth()
  const isOwner = activeTenant?.role === 'owner'

  const [tenant, setTenant] = useState<TenantResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [savedBanner, setSavedBanner] = useState(false)

  const [legalName, setLegalName] = useState('')
  const [tradeName, setTradeName] = useState('')
  const [cifNif, setCifNif] = useState('')
  const [defaultTimezone, setDefaultTimezone] = useState('Europe/Madrid')
  const [defaultLocale, setDefaultLocale] = useState<'zh-CN' | 'es-ES'>('zh-CN')

  const loadTenant = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const { data, error } = await apiClient.GET('/api/v1/tenants/current')
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setTenant(data)
      setLegalName(data.legal_name)
      setTradeName(data.trade_name)
      setCifNif(data.cif_nif)
      setDefaultTimezone(data.default_timezone)
      setDefaultLocale(data.default_locale === 'es-ES' ? 'es-ES' : 'zh-CN')
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    void loadTenant()
  }, [loadTenant, activeTenant?.tenant_id])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!isOwner) return
    setSaving(true)
    setErrorBanner(null)
    setSavedBanner(false)
    try {
      const { data, error } = await apiClient.PATCH('/api/v1/tenants/current', {
        body: {
          legal_name: legalName,
          trade_name: tradeName,
          cif_nif: cifNif,
          default_timezone: defaultTimezone,
          default_locale: defaultLocale,
        },
      })
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setTenant(data)
      setSavedBanner(true)
      await refreshProfile(data.id)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h1 className="text-lg font-semibold text-slate-900">
          {t('organization.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('organization.subtitle')}</p>
      </div>

      {!isOwner && (
        <div className="rounded-md border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-600">
          {t('organization.readOnlyBanner')}
        </div>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {savedBanner && (
        <div
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-xs text-emerald-700"
        >
          {t('organization.savedNotice')}
        </div>
      )}

      {/* Template 2: Left Anchor Nav + Right Two-Column Form Cards */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <aside className="lg:col-span-3">
          <nav className="flex flex-row gap-1 lg:flex-col">
            <a
              href="#org-profile"
              className="rounded-md bg-white px-3 py-2 text-sm font-medium text-slate-900 ring-1 ring-slate-200"
            >
              {t('organization.navProfile')}
            </a>
            <a
              href="#org-compliance"
              className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-slate-900"
            >
              {t('organization.navCompliance')}
            </a>
          </nav>
        </aside>

        <form
          onSubmit={handleSubmit}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && isImeComposing(e)) {
              e.preventDefault()
            }
          }}
          className="space-y-4 lg:col-span-9"
        >
          {loading || !tenant ? (
            <Card className="border-slate-200 bg-white shadow-none">
              <CardContent className="space-y-4 p-6">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
              </CardContent>
            </Card>
          ) : (
            <>
              <Card
                id="org-profile"
                className="scroll-mt-20 border-slate-200 bg-white shadow-none"
              >
                <CardHeader>
                  <CardTitle className="text-base font-semibold">
                    {t('organization.navProfile')}
                  </CardTitle>
                  <CardDescription>
                    {t('organization.subtitle')}
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="org-legal-name">
                      {t('onboarding.legalName')}
                    </Label>
                    <Input
                      id="org-legal-name"
                      required
                      disabled={!isOwner}
                      value={legalName}
                      onChange={(e) => setLegalName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="org-trade-name">
                      {t('onboarding.tradeName')}
                    </Label>
                    <Input
                      id="org-trade-name"
                      required
                      disabled={!isOwner}
                      value={tradeName}
                      onChange={(e) => setTradeName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="org-cif">{t('onboarding.cifNif')}</Label>
                    <Input
                      id="org-cif"
                      required
                      disabled={!isOwner}
                      spellCheck={false}
                      className="uppercase tabular-nums"
                      value={cifNif}
                      onChange={(e) => setCifNif(e.target.value)}
                    />
                  </div>
                </CardContent>
              </Card>

              <Card
                id="org-compliance"
                className="scroll-mt-20 border-slate-200 bg-white shadow-none"
              >
                <CardHeader>
                  <CardTitle className="text-base font-semibold">
                    {t('organization.navCompliance')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="space-y-1.5">
                    <Label htmlFor="org-tz">
                      {t('onboarding.defaultTimezone')}
                    </Label>
                    <Select
                      disabled={!isOwner}
                      value={defaultTimezone}
                      onValueChange={setDefaultTimezone}
                    >
                      <SelectTrigger id="org-tz" className="w-full">
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
                    <Label htmlFor="org-locale">
                      {t('onboarding.defaultLocale')}
                    </Label>
                    <Select
                      disabled={!isOwner}
                      value={defaultLocale}
                      onValueChange={(value) =>
                        setDefaultLocale(value as 'zh-CN' | 'es-ES')
                      }
                    >
                      <SelectTrigger id="org-locale" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="zh-CN">中文 (zh-CN)</SelectItem>
                        <SelectItem value="es-ES">Español (es-ES)</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </CardContent>
              </Card>

              {isOwner && (
                <div className="flex justify-end">
                  <Button type="submit" disabled={saving}>
                    {saving
                      ? t('organization.saving')
                      : t('organization.save')}
                  </Button>
                </div>
              )}
            </>
          )}
        </form>
      </div>
    </div>
  )
}
