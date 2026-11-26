import { useCallback, useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Eraser, Store as StoreIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  AttendanceEventType,
  ClockAttendanceResponse,
  StoreResponse,
} from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
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
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'

type KioskAction = Exclude<AttendanceEventType, 'correction'>

const KIOSK_ACTIONS: Array<{
  type: KioskAction
  variant: 'default' | 'outline'
}> = [
  { type: 'clock_in', variant: 'default' },
  { type: 'break_start', variant: 'outline' },
  { type: 'break_end', variant: 'outline' },
  { type: 'clock_out', variant: 'default' },
]

export function KioskPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()

  const canOperateKiosk =
    activeTenant?.role === 'owner' ||
    activeTenant?.role === 'hr' ||
    activeTenant?.role === 'store_manager'

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loadingStores, setLoadingStores] = useState(true)
  const [selectedStoreId, setSelectedStoreId] = useState<string>('')
  const [employeeCode, setEmployeeCode] = useState('')
  const [submittingAction, setSubmittingAction] = useState<KioskAction | null>(
    null,
  )
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<ClockAttendanceResponse | null>(null)
  const [countdown, setCountdown] = useState<number>(0)

  const locale = i18n.language || 'zh-CN'

  const clearKioskState = useCallback(() => {
    setEmployeeCode('')
    setReceipt(null)
    setErrorMessage(null)
    setCountdown(0)
  }, [])

  useEffect(() => {
    if (!canOperateKiosk) {
      setLoadingStores(false)
      return
    }
    let cancelled = false
    const loadStores = async () => {
      setLoadingStores(true)
      try {
        const res = await apiClient.GET('/api/v1/stores')
        if (cancelled) return
        if (res.error) {
          setErrorMessage(extractApiError(res.error))
          return
        }
        if (res.data) {
          const activeStores = res.data.items.filter((item) => item.is_active)
          setStores(activeStores)
          if (activeStores.length > 0) {
            setSelectedStoreId((prev) =>
              activeStores.some((s) => s.id === prev)
                ? prev
                : activeStores[0].id,
            )
          } else {
            setSelectedStoreId('')
          }
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(extractApiError(err))
        }
      } finally {
        if (!cancelled) {
          setLoadingStores(false)
        }
      }
    }
    void loadStores()
    return () => {
      cancelled = true
    }
  }, [canOperateKiosk, activeTenant?.tenant_id])

  // Mandatory 3-second auto-clear after any successful punch
  useEffect(() => {
    if (!receipt) {
      return
    }
    setCountdown(3)
    const intervalId = window.setInterval(() => {
      setCountdown((prev) => (prev <= 1 ? 0 : prev - 1))
    }, 1000)
    const timeoutId = window.setTimeout(() => {
      clearKioskState()
    }, 3000)

    return () => {
      window.clearInterval(intervalId)
      window.clearTimeout(timeoutId)
    }
  }, [receipt, clearKioskState])

  const selectedStore = useMemo(
    () => stores.find((s) => s.id === selectedStoreId) ?? null,
    [stores, selectedStoreId],
  )

  const handlePunch = async (action: KioskAction) => {
    setErrorMessage(null)
    if (!selectedStoreId) {
      setErrorMessage(t('kiosk.storeRequired'))
      return
    }
    const trimmedCode = employeeCode.trim()
    if (!trimmedCode) {
      setErrorMessage(t('kiosk.employeeCodeRequired'))
      return
    }

    setSubmittingAction(action)
    try {
      const res = await apiClient.POST('/api/v1/attendance/clock', {
        body: {
          event_type: action,
          source: 'kiosk',
          store_id: selectedStoreId,
          employee_code: trimmedCode,
          work_mode: 'onsite',
        },
      })

      if (res.error || !res.data) {
        setErrorMessage(extractApiError(res.error, t('errors.GENERIC')))
        return
      }

      setReceipt(res.data)
      setEmployeeCode('')
    } catch (err) {
      setErrorMessage(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSubmittingAction(null)
    }
  }

  if (!canOperateKiosk) {
    return (
      <div className="mx-auto max-w-xl rounded-md border border-slate-200 bg-white p-6">
        <h1 className="text-lg font-semibold text-slate-900">
          {t('kiosk.unauthorizedTitle')}
        </h1>
        <p className="mt-1.5 text-sm text-slate-600">
          {t('kiosk.unauthorizedDescription')}
        </p>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-slate-900">
          {t('kiosk.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('kiosk.subtitle')}</p>
      </div>

      <section className="rounded-md border border-slate-200 bg-white p-5 sm:p-6">
        <div className="space-y-5">
          <div className="space-y-1.5">
            <Label htmlFor="kiosk-store-select">{t('kiosk.selectStore')}</Label>
            {loadingStores ? (
              <Skeleton className="h-9 w-full" />
            ) : stores.length === 0 ? (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-600">
                {t('kiosk.noStores')}
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-2.5">
                <Select
                  value={selectedStoreId}
                  onValueChange={(value) => {
                    setSelectedStoreId(value)
                    setErrorMessage(null)
                  }}
                >
                  <SelectTrigger
                    id="kiosk-store-select"
                    aria-label={t('kiosk.selectStore')}
                    className="min-w-60 flex-1 bg-white"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {stores.map((store) => (
                      <SelectItem key={store.id} value={store.id}>
                        {store.code} · {store.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedStore && (
                  <Badge
                    variant="outline"
                    className="border-slate-200 bg-slate-50 text-slate-600 tabular-nums"
                  >
                    <StoreIcon className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                    {selectedStore.timezone}
                  </Badge>
                )}
              </div>
            )}
          </div>

          {receipt ? (
            <div
              role="status"
              aria-live="polite"
              className="space-y-4 rounded-md border border-emerald-200 bg-emerald-50 p-5"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-start gap-2.5">
                  <CheckCircle2
                    className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700"
                    aria-hidden="true"
                  />
                  <div>
                    <h2 className="text-base font-semibold text-emerald-900">
                      {t('kiosk.receiptTitle')}
                    </h2>
                    <p className="mt-0.5 text-sm font-medium text-emerald-800">
                      {receipt.employee_name}{' '}
                      <span className="font-mono text-xs tabular-nums">
                        ({receipt.employee_code})
                      </span>
                    </p>
                  </div>
                </div>
                <Badge
                  variant="outline"
                  className="border-emerald-300 bg-white text-emerald-800"
                >
                  {t(`attendance.actions.${receipt.event.event_type}`)}
                </Badge>
              </div>

              <div className="grid grid-cols-1 gap-2 rounded-md border border-emerald-200 bg-white px-3.5 py-2.5 text-xs text-slate-700 sm:grid-cols-2">
                <div>
                  <span className="text-slate-500">
                    {t('kiosk.selectStore')}:{' '}
                  </span>
                  <span className="font-medium text-slate-900">
                    {receipt.store_name}
                  </span>
                </div>
                <div className="tabular-nums">
                  <span className="text-slate-500">
                    {t('kiosk.receiptTime')}:{' '}
                  </span>
                  <span className="font-medium text-slate-900">
                    {formatUtcDateTime(
                      receipt.event.occurred_at_utc,
                      locale,
                      receipt.event.store_timezone,
                    )}
                  </span>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="text-xs font-medium text-emerald-800 tabular-nums">
                  {t('kiosk.autoClearNotice', { seconds: countdown })}
                </span>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={clearKioskState}
                >
                  <Eraser className="h-4 w-4" aria-hidden="true" />
                  {t('kiosk.clearNow')}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="kiosk-employee-code">
                  {t('kiosk.employeeCode')}
                </Label>
                <Input
                  id="kiosk-employee-code"
                  name="employee_code"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  aria-label={t('kiosk.employeeCode')}
                  value={employeeCode}
                  onChange={(e) => {
                    setEmployeeCode(e.target.value)
                    if (errorMessage) setErrorMessage(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && isImeComposing(e)) {
                      e.preventDefault()
                    }
                  }}
                  placeholder={t('kiosk.employeeCodePlaceholder')}
                  className="h-11 font-mono text-base tabular-nums"
                  disabled={stores.length === 0 || submittingAction !== null}
                />
              </div>

              {errorMessage && (
                <div
                  role="alert"
                  aria-live="polite"
                  className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700"
                >
                  {errorMessage}
                </div>
              )}

              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {KIOSK_ACTIONS.map(({ type, variant }) => (
                  <Button
                    key={type}
                    type="button"
                    variant={variant}
                    className="min-h-12 touch-manipulation px-4 text-sm font-medium"
                    disabled={stores.length === 0 || submittingAction !== null}
                    onClick={() => void handlePunch(type)}
                  >
                    {submittingAction === type
                      ? t('kiosk.submitting')
                      : t(`attendance.actions.${type}`)}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
