import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, CalendarCog, History } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  EmployeeEntitlementResponse,
  EmployeeResponse,
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
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface LeaveEntitlementDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  employees?: EmployeeResponse[]
  stores?: StoreResponse[]
  lockedEmployee?: EmployeeResponse
  defaultYear?: number
  onSuccess?: () => void | Promise<void>
}

export function LeaveEntitlementDialog({
  open,
  onOpenChange,
  employees,
  stores,
  lockedEmployee,
  defaultYear,
  onSuccess,
}: LeaveEntitlementDialogProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const role = activeTenant?.role
  const lockedEmployeeId = lockedEmployee?.id
  const canBulk =
    !lockedEmployeeId && (role === 'owner' || role === 'hr')

  const [requestedMode, setMode] = useState<'single' | 'bulk'>('single')
  const mode = canBulk ? requestedMode : 'single'
  const [yearInput, setYearInput] = useState(
    () => String(defaultYear ?? new Date().getUTCFullYear()),
  )
  const year = Number(yearInput)
  const validYear = Number.isInteger(year) && year >= 2020 && year <= 2100
  const [employeeId, setSelectedEmployeeId] = useState('')
  const selectedEmployeeId = lockedEmployeeId ?? (employeeId || employees?.[0]?.id || '')
  const [bulkStoreId, setBulkStoreId] = useState<string>('all')

  const [baseDays, setBaseDays] = useState<string>('30')
  const [carryoverDays, setCarryoverDays] = useState<string>('0')
  const [compOffDays, setCompOffDays] = useState<string>('0')
  const [reason, setReason] = useState<string>('')

  const [entitlement, setEntitlement] = useState<{
    key: string
    data: EmployeeEntitlementResponse | undefined
  }>()
  const [loadError, setLoadError] = useState<{ key: string; message: string }>()
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [submitAttempted, setSubmitAttempted] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  const targetId = mode === 'single' && validYear ? selectedEmployeeId : ''
  const loadKey = targetId ? `${activeTenant?.tenant_id}/${targetId}/${year}/${loadAttempt}` : ''
  const loaded = entitlement?.key === loadKey
  const currentEntitlement = loaded ? entitlement.data : undefined
  const currentLoadError = loadError?.key === loadKey ? loadError.message : null
  const loading = Boolean(loadKey && !loaded && !currentLoadError)
  const waitingForEntitlement = mode === 'single' && !loaded

  useEffect(() => {
    if (!open) return
    setSubmitAttempted(false)
    setApiError(null)
    setReason('')
    setYearInput(String(defaultYear ?? new Date().getUTCFullYear()))
  }, [open, defaultYear, lockedEmployeeId])

  useEffect(() => {
    setEntitlement(undefined)
    setLoadError(undefined)
    if (!open || !targetId) return
    const request = new AbortController()
    const { signal } = request

    async function loadEntitlement() {
      try {
        const res = await apiClient.GET('/api/v1/leaves/entitlements', {
          params: {
            query: { year, employee_id: targetId },
          },
          signal,
        })
        if (signal.aborted) return
        if (res.error || !res.data) {
          setLoadError({
            key: loadKey,
            message: extractApiError(res.error, t('errors.GENERIC')),
          })
          return
        }
        const current = res.data.items.find((item) => item.employee_id === targetId)
        setBaseDays(String(current?.base_vacation_days ?? 30))
        setCarryoverDays(String(current?.carryover_days ?? 0))
        setCompOffDays(String(current?.comp_off_days ?? 0))
        setEntitlement({ key: loadKey, data: current })
      } catch (err) {
        if (!signal.aborted) {
          setLoadError({
            key: loadKey,
            message: extractApiError(err, t('errors.GENERIC')),
          })
        }
      }
    }

    void loadEntitlement()
    return () => request.abort()
  }, [open, year, targetId, loadKey, t])

  const parsedBase = Number(baseDays)
  const parsedCarryover = Number(carryoverDays)
  const parsedCompOff = Number(compOffDays)

  const invalidBaseDays = !Number.isFinite(parsedBase) || parsedBase < 30
  const invalidCarryover = !Number.isFinite(parsedCarryover) || parsedCarryover < 0
  const invalidCompOff = !Number.isFinite(parsedCompOff) || parsedCompOff < 0
  const missingReason = !reason.trim()

  const totalPreview =
    (Number.isFinite(parsedBase) ? parsedBase : 0) +
    (Number.isFinite(parsedCarryover) ? parsedCarryover : 0) +
    (Number.isFinite(parsedCompOff) ? parsedCompOff : 0)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitAttempted(true)
    setApiError(null)
    if (
      submitting ||
      !validYear ||
      waitingForEntitlement ||
      invalidBaseDays ||
      invalidCarryover ||
      invalidCompOff ||
      missingReason
    ) {
      return
    }

    setSubmitting(true)
    try {
      if (mode === 'bulk' && canBulk) {
        const res = await apiClient.POST('/api/v1/leaves/entitlements/bulk', {
          body: {
            year,
            base_vacation_days: parsedBase,
            carryover_days: parsedCarryover,
            comp_off_days: parsedCompOff,
            reason: reason.trim(),
            store_id: bulkStoreId !== 'all' ? bulkStoreId : null,
          },
        })
        if (res.error) {
          setApiError(extractApiError(res.error, t('errors.GENERIC')))
          return
        }
      } else {
        const res = await apiClient.PUT(
          '/api/v1/leaves/entitlements/{employee_id}',
          {
            params: { path: { employee_id: selectedEmployeeId } },
            body: {
              year,
              base_vacation_days: parsedBase,
              carryover_days: parsedCarryover,
              comp_off_days: parsedCompOff,
              reason: reason.trim(),
            },
          },
        )
        if (res.error) {
          setApiError(extractApiError(res.error, t('errors.GENERIC')))
          return
        }
      }

      onOpenChange(false)
      await onSuccess?.()
    } catch (err) {
      setApiError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col overflow-y-auto bg-white sm:max-w-lg"
      >
        <SheetHeader className="border-b border-slate-200 px-6 py-4">
          <SheetTitle className="flex items-center gap-2">
            <CalendarCog className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
            {isEs ? 'Ajustar saldo de vacaciones' : '调整假期额度'}
          </SheetTitle>
          <SheetDescription>
            {isEs
              ? 'Conforme al Art. 38 del Estatuto de los Trabajadores (mínimo 30 días naturales/año).'
              : '依据西班牙《工人法》Art. 38，员工每年享有不少于 30 个自然日的带薪年假，所有调整留痕可审计。'}
          </SheetDescription>
        </SheetHeader>

        <form
          noValidate
          onSubmit={(e) => void handleSubmit(e)}
          className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
        >
          {canBulk && (
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1">
              <button
                type="button"
                onClick={() => setMode('single')}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                  mode === 'single'
                    ? 'bg-[#2563EB] text-white'
                    : 'text-slate-600 hover:text-slate-900',
                )}
              >
                {isEs ? 'Empleado individual' : '单人调整'}
              </button>
              <button
                type="button"
                onClick={() => setMode('bulk')}
                className={cn(
                  'rounded-md px-3 py-1 text-xs font-medium transition-colors',
                  mode === 'bulk'
                    ? 'bg-[#2563EB] text-white'
                    : 'text-slate-600 hover:text-slate-900',
                )}
              >
                {isEs ? 'Ajuste masivo' : '批量初始化/调整'}
              </button>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ent-year">
                {isEs ? 'Año fiscal' : '适用年份'}
              </Label>
              <Input
                id="ent-year"
                type="number"
                min={2020}
                max={2100}
                value={yearInput}
                onChange={(e) => setYearInput(e.target.value)}
                aria-invalid={!validYear}
                className="tabular-nums"
              />
            </div>

            {mode === 'single' ? (
              <div className="space-y-1.5">
                <Label htmlFor="ent-employee">
                  {isEs ? 'Empleado' : '员工'}
                </Label>
                {lockedEmployee ? (
                  <Input
                    id="ent-employee"
                    disabled
                    value={`${lockedEmployee.employee_code} · ${lockedEmployee.full_name}`}
                  />
                ) : (
                  <Select
                    value={selectedEmployeeId}
                    onValueChange={setSelectedEmployeeId}
                  >
                    <SelectTrigger id="ent-employee" className="w-full bg-white">
                      <SelectValue placeholder={isEs ? 'Seleccionar' : '选择员工'} />
                    </SelectTrigger>
                    <SelectContent>
                      {employees?.map((emp) => (
                        <SelectItem key={emp.id} value={emp.id}>
                          {emp.employee_code} · {emp.full_name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              </div>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="ent-store">
                  {isEs ? 'Centro de trabajo' : '适用门店'}
                </Label>
                <Select value={bulkStoreId} onValueChange={setBulkStoreId}>
                  <SelectTrigger id="ent-store" className="w-full bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">
                      {isEs ? 'Toda la empresa' : '全企业所有员工'}
                    </SelectItem>
                    {stores?.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.code} · {s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="ent-base">
                {isEs ? 'Base anual' : '基础年假 (天)'}
              </Label>
              <Input
                id="ent-base"
                type="number"
                min={30}
                step="0.5"
                value={waitingForEntitlement ? '' : baseDays}
                disabled={waitingForEntitlement}
                onChange={(e) => setBaseDays(e.target.value)}
                aria-invalid={submitAttempted && invalidBaseDays}
                className={cn(
                  'tabular-nums',
                  submitAttempted && invalidBaseDays && 'border-rose-400',
                )}
              />
              <p className="text-[11px] text-slate-500">
                {isEs ? 'Art. 38 ET · Mín. 30d' : 'Art. 38 ET · 最低 30 天'}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ent-carryover">
                {isEs ? 'Arrastre' : '上年结转'}
              </Label>
              <Input
                id="ent-carryover"
                type="number"
                min={0}
                step="0.5"
                value={waitingForEntitlement ? '' : carryoverDays}
                disabled={waitingForEntitlement}
                onChange={(e) => setCarryoverDays(e.target.value)}
                className="tabular-nums"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ent-compoff">
                {isEs ? 'Compensatorios' : '补休/调休'}
              </Label>
              <Input
                id="ent-compoff"
                type="number"
                min={0}
                step="0.5"
                value={waitingForEntitlement ? '' : compOffDays}
                disabled={waitingForEntitlement}
                onChange={(e) => setCompOffDays(e.target.value)}
                className="tabular-nums"
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-blue-200 bg-blue-50/60 px-3.5 py-2.5 text-xs">
            <span className="font-medium text-slate-700">
              {isEs ? 'Total asignado previsto' : '调整后总可用额度'}
            </span>
            <span className="text-base font-bold text-[#2563EB] tabular-nums">
              {waitingForEntitlement ? '—' : `${totalPreview} ${isEs ? 'días' : '天'}`}
            </span>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ent-reason">
              {isEs ? 'Motivo del ajuste' : '调整原因'}
              <span className="ml-0.5 text-rose-500" aria-hidden="true">
                *
              </span>
            </Label>
            <Input
              id="ent-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
              }}
              placeholder={
                isEs
                  ? 'Ej. Acuerdo de arrastre 2025 / Compensación festivo'
                  : '例如：2025 年未休年假结转 / 节假日加班调休'
              }
              aria-invalid={submitAttempted && missingReason}
              className={cn(submitAttempted && missingReason && 'border-rose-400')}
            />
            {submitAttempted && missingReason && (
              <p className="text-xs text-rose-600">
                {isEs
                  ? 'El motivo es obligatorio para el registro de auditoría.'
                  : '请填写调整原因以便留存合规审计日志。'}
              </p>
            )}
          </div>

          {submitAttempted && invalidBaseDays && (
            <div className="flex items-center gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
              <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>
                {isEs
                  ? 'La base anual no puede ser inferior a 30 días (Art. 38 ET).'
                  : '基础年假天数不得低于 30 天（西班牙《工人法》Art. 38）。'}
              </span>
            </div>
          )}

          {(currentLoadError || apiError) && (
            <div
              role="alert"
              className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
            >
              {currentLoadError || apiError}
              {currentLoadError && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => setLoadAttempt((attempt) => attempt + 1)}
                >
                  {isEs ? 'Reintentar' : '重试'}
                </Button>
              )}
            </div>
          )}

          {targetId && !currentLoadError && (
            <>
              <Separator />
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
                  <History className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
                  <span>{isEs ? 'Historial de ajustes' : '不可篡改调整审计日志'}</span>
                </div>
                {loading ? (
                  <Skeleton className="h-20 w-full rounded-lg" />
                ) : !currentEntitlement ||
                  currentEntitlement.adjustments.length === 0 ? (
                  <p className="rounded-lg border border-dashed border-slate-200 px-3 py-3 text-xs text-slate-500">
                    {isEs
                      ? 'Sin ajustes registrados para este año.'
                      : '本年度暂无人工调整记录（默认按 30 天基础年假计算）。'}
                  </p>
                ) : (
                  <ul className="max-h-48 space-y-2 overflow-y-auto pr-1">
                    {currentEntitlement.adjustments.map((adj) => (
                      <li
                        key={adj.id}
                        className="rounded-lg border border-slate-200/80 bg-slate-50/70 px-3 py-2 text-xs"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <Badge
                            variant="outline"
                            className="border-slate-200 bg-white text-[10px] text-slate-700"
                          >
                            {adj.adjustment_type}
                          </Badge>
                          <span className="font-semibold text-slate-900 tabular-nums">
                            {adj.previous_total_days} → {adj.new_total_days} d (
                            {adj.delta_days >= 0 ? `+${adj.delta_days}` : adj.delta_days})
                          </span>
                        </div>
                        <p className="mt-1 text-slate-700">{adj.reason}</p>
                        <p className="mt-0.5 text-[11px] text-slate-400 tabular-nums">
                          {formatUtcDateTime(adj.created_at, locale)}
                        </p>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </>
          )}

          <SheetFooter className="flex gap-2 border-t border-slate-200 px-0 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={submitting || !validYear || waitingForEntitlement}
              className="bg-[#2563EB] text-white hover:bg-blue-700"
            >
              {submitting
                ? isEs
                  ? 'Guardando...'
                  : '正在保存...'
                : isEs
                  ? 'Guardar ajuste'
                  : '保存调整'}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
