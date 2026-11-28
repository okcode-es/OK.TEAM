import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Eraser,
  Lock,
  ShieldCheck,
  Store as StoreIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  AttendanceEventType,
  ClockAttendanceResponse,
  StoreResponse,
} from '@/api/types'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
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
import {
  canRoleOperateKiosk,
  clearKioskLock,
  createKioskLock,
  KIOSK_PIN_REGEX,
  verifyKioskUnlockPin,
  type KioskLockRecord,
} from '@/features/attendance/kioskLock'
import { useAuth } from '@/features/auth/AuthContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

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

export interface KioskPageProps {
  lockedRecord: KioskLockRecord | null
  onLockStateChange: () => void
  onExit: () => void
}

export function KioskPage({
  lockedRecord,
  onLockStateChange,
  onExit,
}: KioskPageProps) {
  const { t, i18n } = useTranslation()
  const { profile, activeTenant } = useAuth()

  const canOperateKiosk = canRoleOperateKiosk(activeTenant?.role)
  const isLocked = lockedRecord !== null

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loadingStores, setLoadingStores] = useState(true)
  const [storesLoaded, setStoresLoaded] = useState(false)
  const [selectedStoreId, setSelectedStoreId] = useState<string>(
    () => lockedRecord?.storeId ?? '',
  )
  const [employeeCode, setEmployeeCode] = useState('')
  const [kioskPin, setKioskPin] = useState('')
  const [submittingAction, setSubmittingAction] = useState<KioskAction | null>(
    null,
  )
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [receipt, setReceipt] = useState<ClockAttendanceResponse | null>(null)
  const [countdown, setCountdown] = useState<number>(0)

  // Setup lock PIN form state (unlocked mode)
  const [setupPin, setSetupPin] = useState('')
  const [setupPinError, setSetupPinError] = useState<string | null>(null)
  const [lockingTerminal, setLockingTerminal] = useState(false)

  // Unlock modal state (locked mode)
  const [unlockModalOpen, setUnlockModalOpen] = useState(false)
  const [unlockPin, setUnlockPin] = useState('')
  const [unlockError, setUnlockError] = useState<string | null>(null)
  const [verifyingPin, setVerifyingPin] = useState(false)

  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')

  const clearKioskState = useCallback(() => {
    setEmployeeCode('')
    setKioskPin('')
    setReceipt(null)
    setErrorMessage(null)
    setCountdown(0)
  }, [])

  // Keep selectedStoreId synced with lockedRecord whenever locked
  useEffect(() => {
    if (lockedRecord?.storeId) {
      setSelectedStoreId(lockedRecord.storeId)
    }
  }, [lockedRecord?.storeId])

  useEffect(() => {
    if (!canOperateKiosk) {
      setLoadingStores(false)
      return
    }
    const controller = new AbortController()
    const { signal } = controller

    const loadStores = async () => {
      setLoadingStores(true)
      setStoresLoaded(false)
      try {
        const res = await apiClient.GET('/api/v1/stores', { signal })
        if (signal.aborted) return
        if (res.error) {
          setErrorMessage(extractApiError(res.error))
          return
        }
        if (res.data) {
          const activeStores = res.data.items.filter((item) => item.is_active)
          setStores(activeStores)
          setStoresLoaded(true)
          if (lockedRecord?.storeId) {
            setSelectedStoreId(lockedRecord.storeId)
          } else if (activeStores.length > 0) {
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
        if (!signal.aborted) {
          setErrorMessage(extractApiError(err))
        }
      } finally {
        if (!signal.aborted) {
          setLoadingStores(false)
        }
      }
    }
    void loadStores()
    return () => {
      controller.abort()
    }
  }, [canOperateKiosk, activeTenant?.tenant_id, lockedRecord?.storeId])

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

  const effectiveStoreId = lockedRecord?.storeId ?? selectedStoreId

  const selectedStore = useMemo(
    () => stores.find((s) => s.id === effectiveStoreId) ?? null,
    [stores, effectiveStoreId],
  )

  const lockedStoreUnavailable =
    isLocked && storesLoaded && selectedStore === null

  const lockedStoreDisplayName = selectedStore
    ? `${selectedStore.code} · ${selectedStore.name}`
    : (lockedRecord?.storeName ?? '')

  const handleLockTerminal = async (e: FormEvent) => {
    e.preventDefault()
    setSetupPinError(null)
    if (!activeTenant?.tenant_id || !profile?.user_id) {
      return
    }
    if (!selectedStoreId || !selectedStore) {
      setSetupPinError(t('kiosk.storeRequired'))
      return
    }
    if (!KIOSK_PIN_REGEX.test(setupPin)) {
      setSetupPinError(t('kiosk.pinFormatError'))
      return
    }

    setLockingTerminal(true)
    try {
      await createKioskLock({
        tenantId: activeTenant.tenant_id,
        userId: profile.user_id,
        storeId: selectedStore.id,
        storeName: `${selectedStore.code} · ${selectedStore.name}`,
        pin: setupPin,
      })
      setSetupPin('')
      setSetupPinError(null)
      onLockStateChange()
    } catch {
      setSetupPinError(t('errors.GENERIC'))
    } finally {
      setLockingTerminal(false)
    }
  }

  const handleOpenUnlockModal = () => {
    setUnlockPin('')
    setUnlockError(null)
    setUnlockModalOpen(true)
  }

  const handleCloseUnlockModal = (open: boolean) => {
    if (verifyingPin) return
    setUnlockModalOpen(open)
    if (!open) {
      setUnlockPin('')
      setUnlockError(null)
    }
  }

  const handleUnlockSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!lockedRecord) {
      onExit()
      return
    }
    setUnlockError(null)
    if (!KIOSK_PIN_REGEX.test(unlockPin)) {
      setUnlockError(t('kiosk.pinFormatError'))
      return
    }

    setVerifyingPin(true)
    try {
      const ok = await verifyKioskUnlockPin(lockedRecord, unlockPin)
      if (!ok) {
        setUnlockError(t('kiosk.invalidPinError'))
        setUnlockPin('')
        return
      }
      clearKioskLock()
      clearKioskState()
      setUnlockPin('')
      setUnlockError(null)
      setUnlockModalOpen(false)
      onLockStateChange()
      onExit()
    } finally {
      setVerifyingPin(false)
    }
  }

  const handlePunch = async (action: KioskAction) => {
    setErrorMessage(null)
    if (!effectiveStoreId || lockedStoreUnavailable) {
      setErrorMessage(
        lockedStoreUnavailable
          ? t('kiosk.lockedStoreUnavailable')
          : t('kiosk.storeRequired'),
      )
      return
    }
    const trimmedCode = employeeCode.trim()
    if (!trimmedCode) {
      setErrorMessage(t('kiosk.employeeCodeRequired'))
      return
    }
    const trimmedPin = kioskPin.trim()
    if (trimmedPin && !/^\d{4,6}$/.test(trimmedPin)) {
      setErrorMessage(t('kiosk.kioskPinFormatError'))
      return
    }

    setSubmittingAction(action)
    try {
      const res = await apiClient.POST('/api/v1/attendance/clock', {
        body: {
          event_type: action,
          source: 'kiosk',
          store_id: effectiveStoreId,
          employee_code: trimmedCode,
          kiosk_pin: trimmedPin || null,
          work_mode: 'onsite',
        },
      })

      if (res.error || !res.data) {
        if (res.response?.status === 403) {
          setErrorMessage(t('kiosk.kioskPinInvalid'))
        } else {
          setErrorMessage(extractApiError(res.error, t('errors.GENERIC')))
        }
        return
      }

      setReceipt(res.data)
      setEmployeeCode('')
      setKioskPin('')
    } catch (err) {
      setErrorMessage(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSubmittingAction(null)
    }
  }

  if (!canOperateKiosk) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#F3F5F9] p-6">
        <div className="w-full max-w-xl rounded-xl border border-slate-200/75 bg-white p-6 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <h1 className="text-lg font-semibold text-slate-900">
            {t('kiosk.unauthorizedTitle')}
          </h1>
          <p className="mt-1.5 text-sm text-slate-600">
            {t('kiosk.unauthorizedDescription')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen flex-col bg-[#F3F5F9] text-slate-900">
      {/* Standalone Full-Screen Kiosk Header (No AppShell Sidebar) */}
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-slate-800 bg-[#0B1120] px-4 text-white sm:px-6">
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[#2563EB] text-xs font-bold text-white"
            aria-hidden="true"
          >
            ok
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold tracking-tight text-white">
                {activeTenant?.tenant_trade_name ?? t('brand')}
              </span>
              {isLocked && (
                <Badge
                  variant="outline"
                  className="hidden border-emerald-400/40 bg-emerald-950/60 text-emerald-200 sm:inline-flex"
                >
                  <ShieldCheck className="mr-1 h-3 w-3" aria-hidden="true" />
                  {t('kiosk.lockedBadge')}
                </Badge>
              )}
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* Bilingual Switcher for Shared Store Tablet */}
          <div
            role="group"
            aria-label={t('auth.localeShort.zh-CN')}
            className="inline-flex rounded-lg border border-slate-700 bg-slate-900 p-0.5"
          >
            <button
              type="button"
              onClick={() => void i18n.changeLanguage('zh-CN')}
              className={cn(
                'rounded-md px-2 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
                !isEs
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-300 hover:text-white',
              )}
            >
              {t('auth.localeShort.zh-CN')}
            </button>
            <button
              type="button"
              onClick={() => void i18n.changeLanguage('es-ES')}
              className={cn(
                'rounded-md px-2 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
                isEs
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-300 hover:text-white',
              )}
            >
              {t('auth.localeShort.es-ES')}
            </button>
          </div>

          {isLocked ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleOpenUnlockModal}
              data-testid="kiosk-exit-locked-btn"
              className="border-slate-700 bg-slate-900 text-xs font-medium text-slate-100 hover:bg-slate-800 hover:text-white"
            >
              <Lock className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('kiosk.exitTerminal')}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onExit}
              data-testid="kiosk-back-dashboard-btn"
              className="border-slate-700 bg-slate-900 text-xs font-medium text-slate-100 hover:bg-slate-800 hover:text-white"
            >
              <ArrowLeft className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('kiosk.backToDashboard')}
            </Button>
          )}
        </div>
      </header>

      {/* Main Full-Screen Kiosk Content */}
      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col justify-center px-4 py-6 sm:px-6 sm:py-10">
        <div className="space-y-5">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">
              {t('kiosk.title')}
            </h1>
            <p className="mt-1 text-sm text-slate-600">{t('kiosk.subtitle')}</p>
          </div>

          <section className="rounded-xl border border-slate-200/75 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:p-6">
            <div className="space-y-5">
              {/* Store Selector (hidden when locked) or Locked Store Banner */}
              {isLocked ? (
                <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5">
                  <div className="flex min-w-0 items-center gap-2 text-sm">
                    <StoreIcon
                      className="h-4 w-4 shrink-0 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    <span className="text-xs font-medium text-slate-600">
                      {t('kiosk.selectStore')}:
                    </span>
                    <span className="truncate font-semibold text-slate-900">
                      {lockedStoreDisplayName}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {selectedStore && (
                      <Badge
                        variant="outline"
                        className="border-slate-200 bg-white text-slate-600 tabular-nums"
                      >
                        {selectedStore.timezone}
                      </Badge>
                    )}
                    <Badge
                      variant="outline"
                      className="border-emerald-200 bg-emerald-50 text-emerald-700"
                    >
                      <Lock className="mr-1 h-3 w-3" aria-hidden="true" />
                      {t('kiosk.lockedBadge')}
                    </Badge>
                  </div>
                </div>
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="kiosk-store-select">
                    {t('kiosk.selectStore')}
                  </Label>
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
                          setSetupPinError(null)
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
                          <StoreIcon
                            className="mr-1 h-3.5 w-3.5"
                            aria-hidden="true"
                          />
                          {selectedStore.timezone}
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
              )}

              {lockedStoreUnavailable && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-800"
                >
                  <AlertCircle
                    className="mt-0.5 h-4 w-4 shrink-0"
                    aria-hidden="true"
                  />
                  <span>{t('kiosk.lockedStoreUnavailable')}</span>
                </div>
              )}

              {/* Manager PIN Lock Setup Panel (only visible before locking) */}
              {!isLocked && !loadingStores && stores.length > 0 && (
                <form
                  onSubmit={(e) => void handleLockTerminal(e)}
                  className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4"
                >
                  <div className="flex items-start gap-2">
                    <Lock
                      className="mt-0.5 h-4 w-4 shrink-0 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    <div className="space-y-0.5">
                      <h2 className="text-sm font-semibold text-slate-900">
                        {t('kiosk.lockSetupTitle')}
                      </h2>
                      <p className="text-xs text-slate-600">
                        {t('kiosk.lockSetupDescription')}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-col gap-2.5 sm:flex-row sm:items-end">
                    <div className="flex-1 space-y-1.5">
                      <Label
                        htmlFor="kiosk-setup-pin"
                        className="text-xs font-medium text-slate-700"
                      >
                        {t('kiosk.pinLabel')}
                      </Label>
                      <Input
                        id="kiosk-setup-pin"
                        name="kiosk_setup_pin"
                        type="password"
                        inputMode="numeric"
                        maxLength={4}
                        autoComplete="off"
                        value={setupPin}
                        onChange={(e) => {
                          setSetupPin(e.target.value)
                          if (setupPinError) setSetupPinError(null)
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && isImeComposing(e)) {
                            e.preventDefault()
                          }
                        }}
                        placeholder={t('kiosk.pinPlaceholder')}
                        className="h-9 bg-white font-mono tracking-[0.25em] tabular-nums"
                        disabled={lockingTerminal}
                      />
                    </div>
                    <Button
                      type="submit"
                      disabled={lockingTerminal || !selectedStoreId}
                      className="h-9 bg-[#2563EB] px-4 text-xs font-medium text-white hover:bg-blue-700"
                    >
                      <Lock className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                      {lockingTerminal
                        ? t('kiosk.locking')
                        : t('kiosk.lockTerminal')}
                    </Button>
                  </div>

                  {setupPinError && (
                    <div
                      role="alert"
                      aria-live="polite"
                      className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                    >
                      {setupPinError}
                    </div>
                  )}
                </form>
              )}

              {/* Employee Punch Area & 3-Second Auto-Clear Receipt */}
              {receipt ? (
                <div
                  role="status"
                  aria-live="polite"
                  className="space-y-4 rounded-lg border border-emerald-200 bg-emerald-50 p-5"
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
                      <span className="text-slate-600">
                        {t('kiosk.selectStore')}:{' '}
                      </span>
                      <span className="font-medium text-slate-900">
                        {receipt.store_name}
                      </span>
                    </div>
                    <div className="tabular-nums">
                      <span className="text-slate-600">
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
                      disabled={
                        (!isLocked && stores.length === 0) ||
                        lockedStoreUnavailable ||
                        submittingAction !== null
                      }
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="kiosk-employee-pin">
                      {t('kiosk.kioskPinLabel')}
                    </Label>
                    <Input
                      id="kiosk-employee-pin"
                      name="kiosk_pin"
                      type="password"
                      inputMode="numeric"
                      maxLength={6}
                      autoComplete="off"
                      aria-label={t('kiosk.kioskPinLabel')}
                      value={kioskPin}
                      onChange={(e) => {
                        setKioskPin(e.target.value.replace(/\D/g, '').slice(0, 6))
                        if (errorMessage) setErrorMessage(null)
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && isImeComposing(e)) {
                          e.preventDefault()
                        }
                      }}
                      placeholder={t('kiosk.kioskPinPlaceholder')}
                      className="h-11 font-mono text-base tabular-nums"
                      disabled={
                        (!isLocked && stores.length === 0) ||
                        lockedStoreUnavailable ||
                        submittingAction !== null
                      }
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
                        className={cn(
                          'min-h-12 touch-manipulation px-4 text-sm font-medium',
                          variant === 'default' &&
                            'bg-[#2563EB] text-white hover:bg-blue-700',
                        )}
                        disabled={
                          (!isLocked && stores.length === 0) ||
                          lockedStoreUnavailable ||
                          submittingAction !== null
                        }
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
      </main>

      {/* Protected 4-Digit PIN Unlock Modal */}
      <AlertDialog open={unlockModalOpen} onOpenChange={handleCloseUnlockModal}>
        <AlertDialogContent className="bg-white sm:max-w-md">
          <form onSubmit={(e) => void handleUnlockSubmit(e)} className="space-y-4">
            <AlertDialogHeader>
              <AlertDialogTitle className="flex items-center gap-2 text-slate-900">
                <Lock className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                {t('kiosk.unlockModalTitle')}
              </AlertDialogTitle>
              <AlertDialogDescription className="text-slate-600">
                {t('kiosk.unlockModalDescription')}
              </AlertDialogDescription>
            </AlertDialogHeader>

            <div className="space-y-1.5">
              <Label htmlFor="kiosk-unlock-pin" className="text-slate-700">
                {t('kiosk.unlockPinLabel')}
              </Label>
              <Input
                id="kiosk-unlock-pin"
                name="kiosk_unlock_pin"
                type="password"
                inputMode="numeric"
                maxLength={4}
                autoComplete="off"
                autoFocus
                value={unlockPin}
                onChange={(e) => {
                  setUnlockPin(e.target.value)
                  if (unlockError) setUnlockError(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) {
                    e.preventDefault()
                  }
                }}
                placeholder={t('kiosk.pinPlaceholder')}
                className="h-11 text-center font-mono text-lg tracking-[0.35em] tabular-nums"
                disabled={verifyingPin}
              />
            </div>

            {unlockError && (
              <div
                role="alert"
                aria-live="polite"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {unlockError}
              </div>
            )}

            <AlertDialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={verifyingPin}
                onClick={() => handleCloseUnlockModal(false)}
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={verifyingPin}
                className="bg-[#2563EB] text-white hover:bg-blue-700"
              >
                {verifyingPin ? t('kiosk.verifying') : t('kiosk.confirmExit')}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
