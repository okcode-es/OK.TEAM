import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertCircle, Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type { EmployeeResponse, WorkMode } from '@/api/types'
import { Button } from '@/components/ui/button'
import { EmployeeCombobox } from '@/components/EmployeeCombobox'
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
import { useClockState } from '@/features/clock/ClockStateContext'
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface ManualDayClockSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  employees?: EmployeeResponse[]
  canSelectEmployee?: boolean
  defaultEmployeeId?: string
  defaultDate?: string
  onSuccess?: () => void | Promise<void>
}

function parseTimeToMinutes(value: string): number | null {
  if (!value || !value.includes(':')) return null
  const [hStr, mStr] = value.split(':')
  const h = Number(hStr)
  const m = Number(mStr)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null
  return h * 60 + m
}

export function ManualDayClockSheet({
  open,
  onOpenChange,
  employees = [],
  canSelectEmployee = false,
  defaultEmployeeId = '',
  defaultDate,
  onSuccess,
}: ManualDayClockSheetProps) {
  const { t, i18n } = useTranslation()
  const { refreshClockState, employee: selfEmployee, clockState } =
    useClockState()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')

  const todayIso = useMemo(
    () =>
      new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid' }).format(
        new Date(),
      ),
    [],
  )

  const errorBoxRef = useRef<HTMLDivElement | null>(null)
  const [employeeId, setEmployeeId] = useState<string>(defaultEmployeeId)
  const [localDate, setLocalDate] = useState<string>(todayIso)
  const [workMode, setWorkMode] = useState<WorkMode>('onsite')
  const [clockInTime, setClockInTime] = useState<string>('09:00')
  const [breakStartTime, setBreakStartTime] = useState<string>('')
  const [breakEndTime, setBreakEndTime] = useState<string>('')
  const [clockOutTime, setClockOutTime] = useState<string>('')
  const [note, setNote] = useState<string>('')
  const [submitting, setSubmitting] = useState(false)
  const [submitAttempted, setSubmitAttempted] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setSubmitAttempted(false)
    setApiError(null)
    setLocalDate(defaultDate || todayIso)
    if (defaultEmployeeId) {
      setEmployeeId(defaultEmployeeId)
    } else if (selfEmployee?.id) {
      setEmployeeId(selfEmployee.id)
    } else if (employees.length > 0) {
      setEmployeeId(employees[0].id)
    }
  }, [open, defaultDate, todayIso, defaultEmployeeId, selfEmployee?.id, employees])

  const isPastDate = Boolean(localDate && localDate < todayIso)
  const missingDate = !localDate
  const missingClockIn = !clockInTime
  const missingClockOut = isPastDate && !clockOutTime
  const partialBreakStart = Boolean(!breakStartTime && breakEndTime)
  const partialBreakEnd = Boolean(breakStartTime && !breakEndTime)

  const inMin = parseTimeToMinutes(clockInTime)
  const outMin = parseTimeToMinutes(clockOutTime)
  const isOvernight = Boolean(
    inMin !== null && outMin !== null && outMin <= inMin,
  )
  const shiftDurationMin =
    inMin !== null && outMin !== null
      ? isOvernight
        ? outMin + 1440 - inMin
        : outMin - inMin
      : null

  const timeOrderError = useMemo(() => {
    if (inMin === null) return null
    if (shiftDurationMin !== null && shiftDurationMin > 16 * 60) {
      return t('attendance.manualDay.validation.max16Hours')
    }
    if (breakStartTime && breakEndTime) {
      const bsRaw = parseTimeToMinutes(breakStartTime)
      const beRaw = parseTimeToMinutes(breakEndTime)
      if (bsRaw !== null && beRaw !== null) {
        const bs = isOvernight && bsRaw < inMin ? bsRaw + 1440 : bsRaw
        const be = isOvernight && beRaw < inMin ? beRaw + 1440 : beRaw
        if (bs <= inMin) {
          return t('attendance.manualDay.validation.breakAfterIn')
        }
        if (be <= bs) {
          return t('attendance.manualDay.validation.breakEndAfterStart')
        }
        if (outMin !== null) {
          const outTimeline = isOvernight ? outMin + 1440 : outMin
          if (outTimeline <= be) {
            return t('attendance.manualDay.validation.outAfterBreak')
          }
        }
      }
    }
    return null
  }, [inMin, outMin, isOvernight, shiftDurationMin, breakStartTime, breakEndTime, t])

  const missingFieldLabels = useMemo(() => {
    const list: string[] = []
    if (missingDate) list.push(t('attendance.manualDay.date'))
    if (missingClockIn) list.push(t('attendance.manualDay.clockInTime'))
    if (missingClockOut) list.push(t('attendance.manualDay.clockOutTime'))
    if (partialBreakStart) list.push(t('attendance.manualDay.breakStartTime'))
    if (partialBreakEnd) list.push(t('attendance.manualDay.breakEndTime'))
    return list
  }, [
    missingDate,
    missingClockIn,
    missingClockOut,
    partialBreakStart,
    partialBreakEnd,
    t,
  ])

  const validationErrorItems = useMemo(() => {
    const items: string[] = []
    if (missingDate) {
      items.push(
        t('attendance.manualDay.validation.requiredItem', {
          field: t('attendance.manualDay.date'),
        }),
      )
    }
    if (missingClockIn) {
      items.push(
        t('attendance.manualDay.validation.requiredItem', {
          field: t('attendance.manualDay.clockInTime'),
        }),
      )
    }
    if (missingClockOut) {
      items.push(t('attendance.manualDay.validation.clockOutPastRequired'))
    }
    if (partialBreakStart || partialBreakEnd) {
      items.push(t('attendance.manualDay.validation.breakPairRequired'))
    }
    if (timeOrderError) {
      items.push(timeOrderError)
    }
    return items
  }, [
    missingDate,
    missingClockIn,
    missingClockOut,
    partialBreakStart,
    partialBreakEnd,
    timeOrderError,
    t,
  ])

  const hasValidationErrors = validationErrorItems.length > 0

  const isTargetSelf =
    !canSelectEmployee || !employeeId || employeeId === selfEmployee?.id
  const hasOpenShiftToday =
    isTargetSelf &&
    localDate === todayIso &&
    Boolean(clockState && clockState.status !== 'not_clocked_in')

  const suggestedBreakMinutes =
    clockState?.effective_break_policy?.effective_duration_minutes ??
    clockState?.effective_break_policy?.duration_minutes ??
    clockState?.default_break_minutes ??
    30

  const handleBreakStartChange = (val: string) => {
    setBreakStartTime(val)
    if (val && !breakEndTime && suggestedBreakMinutes > 0) {
      const [hStr, mStr] = val.split(':')
      const h = Number(hStr)
      const m = Number(mStr)
      if (Number.isFinite(h) && Number.isFinite(m)) {
        const total = h * 60 + m + suggestedBreakMinutes
        const endH = Math.floor(total / 60) % 24
        const endM = total % 60
        setBreakEndTime(
          `${String(endH).padStart(2, '0')}:${String(endM).padStart(2, '0')}`,
        )
      }
    }
  }

  useEffect(() => {
    if ((submitAttempted && hasValidationErrors) || apiError) {
      errorBoxRef.current?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    }
  }, [submitAttempted, hasValidationErrors, apiError])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitAttempted(true)
    setApiError(null)
    if (hasValidationErrors) return

    setSubmitting(true)
    try {
      const targetEmpId =
        canSelectEmployee && employeeId ? employeeId : undefined
      const res = await apiClient.POST('/api/v1/attendance/manual-day', {
        body: {
          employee_id: targetEmpId ?? null,
          local_date: localDate,
          clock_in_time: clockInTime,
          break_start_time: breakStartTime || null,
          break_end_time: breakEndTime || null,
          clock_out_time: clockOutTime || null,
          work_mode: workMode,
          note: note.trim(),
        },
      })
      if (res.error || !res.data) {
        setApiError(extractApiError(res.error, t('errors.GENERIC')))
        return
      }
      onOpenChange(false)
      setSubmitAttempted(false)
      setNote('')
      await refreshClockState()
      await onSuccess?.()
    } catch (err) {
      setApiError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next)
        if (!next) {
          setSubmitAttempted(false)
          setApiError(null)
        }
      }}
    >
      <SheetContent
        side="right"
        className="w-full overflow-y-auto bg-white sm:max-w-md"
      >
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
            {t('attendance.manualDay.title')}
          </SheetTitle>
          <SheetDescription>
            {t('attendance.manualDay.description')}
          </SheetDescription>
        </SheetHeader>

        <form
          noValidate
          onSubmit={(e) => void handleSubmit(e)}
          className="mt-4 space-y-4 px-4"
        >
          {hasOpenShiftToday && (
            <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                aria-hidden="true"
              />
              <span>
                {isEs
                  ? 'Ya tiene un turno abierto hoy. Para modificarlo, finalice la jornada actual o use una corrección de fichaje.'
                  : '今日已有进行中的打卡班次，请先在工作台结束当前班次，或前往考勤台账发起补卡修正。'}
              </span>
            </div>
          )}

          {missingFieldLabels.length > 0 && (
            <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                aria-hidden="true"
              />
              <span>
                {t('attendance.manualDay.validation.missingGuidance', {
                  fields: missingFieldLabels.join(isEs ? ', ' : '、'),
                })}
              </span>
            </div>
          )}

          {canSelectEmployee && employees.length > 0 && (
            <div className="space-y-1.5">
              <Label htmlFor="manual-day-emp">
                {t('attendance.manualDay.employee')}
              </Label>
              <EmployeeCombobox
                id="manual-day-emp"
                employees={employees}
                value={employeeId}
                onValueChange={setEmployeeId}
                ariaLabel={t('attendance.manualDay.employee')}
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="manual-day-date">
                {t('attendance.manualDay.date')}
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              </Label>
              <Input
                id="manual-day-date"
                type="date"
                max={todayIso}
                value={localDate}
                onChange={(e) => setLocalDate(e.target.value)}
                aria-invalid={submitAttempted && missingDate}
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    missingDate &&
                    'border-rose-400 focus-visible:ring-rose-500',
                )}
              />
              {submitAttempted && missingDate && (
                <p className="text-xs text-rose-600">
                  {t('attendance.manualDay.validation.requiredItem', {
                    field: t('attendance.manualDay.date'),
                  })}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="manual-day-mode">
                {t('clock.workModeLabel')}
              </Label>
              <Select
                value={workMode}
                onValueChange={(v) => setWorkMode(v as WorkMode)}
              >
                <SelectTrigger id="manual-day-mode" className="w-full bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="onsite">
                    {t('clock.workMode.onsite')}
                  </SelectItem>
                  <SelectItem value="remote">
                    {t('clock.workMode.remote')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="manual-day-in">
                {t('attendance.manualDay.clockInTime')}
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              </Label>
              <Input
                id="manual-day-in"
                type="time"
                value={clockInTime}
                onChange={(e) => setClockInTime(e.target.value)}
                aria-invalid={submitAttempted && missingClockIn}
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    missingClockIn &&
                    'border-rose-400 focus-visible:ring-rose-500',
                )}
              />
              {submitAttempted && missingClockIn && (
                <p className="text-xs text-rose-600">
                  {t('attendance.manualDay.validation.requiredItem', {
                    field: t('attendance.manualDay.clockInTime'),
                  })}
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="manual-day-out">
                {t('attendance.manualDay.clockOutTime')}
                {isPastDate && (
                  <span className="ml-0.5 text-rose-500" aria-hidden="true">
                    *
                  </span>
                )}
              </Label>
              <Input
                id="manual-day-out"
                type="time"
                value={clockOutTime}
                onChange={(e) => setClockOutTime(e.target.value)}
                aria-invalid={submitAttempted && missingClockOut}
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    missingClockOut &&
                    'border-rose-400 focus-visible:ring-rose-500',
                )}
              />
              {submitAttempted && missingClockOut && (
                <p className="text-xs text-rose-600">
                  {t('attendance.manualDay.validation.clockOutPastRequired')}
                </p>
              )}
              {isOvernight &&
                shiftDurationMin !== null &&
                shiftDurationMin <= 16 * 60 && (
                  <p className="text-[11px] font-medium text-[#2563EB] tabular-nums">
                    {t('attendance.manualDay.overnightHint', {
                      hours: (shiftDurationMin / 60).toFixed(1),
                    })}
                  </p>
                )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="manual-day-break-start">
                {t('attendance.manualDay.breakStartTime')}
              </Label>
              <Input
                id="manual-day-break-start"
                type="time"
                value={breakStartTime}
                onChange={(e) => handleBreakStartChange(e.target.value)}
                aria-invalid={submitAttempted && partialBreakStart}
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    partialBreakStart &&
                    'border-rose-400 focus-visible:ring-rose-500',
                )}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="manual-day-break-end">
                {t('attendance.manualDay.breakEndTime')}
              </Label>
              <Input
                id="manual-day-break-end"
                type="time"
                value={breakEndTime}
                onChange={(e) => setBreakEndTime(e.target.value)}
                aria-invalid={submitAttempted && partialBreakEnd}
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    partialBreakEnd &&
                    'border-rose-400 focus-visible:ring-rose-500',
                )}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="manual-day-note">
              {t('attendance.manualDay.note')}
            </Label>
            <Input
              id="manual-day-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) {
                  e.preventDefault()
                }
              }}
              placeholder={t('attendance.manualDay.notePlaceholder')}
            />
          </div>

          <Separator />

          <div ref={errorBoxRef} className="space-y-2">
            {submitAttempted && hasValidationErrors && (
              <div
                role="alert"
                className="space-y-1 rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-700"
              >
                <div className="font-semibold">
                  {t('attendance.manualDay.validation.summaryTitle')}
                </div>
                <ul className="list-inside list-disc space-y-0.5">
                  {validationErrorItems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {apiError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {apiError}
              </div>
            )}
          </div>

          <SheetFooter className="flex gap-2 px-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={submitting}
              className="bg-[#2563EB] text-white hover:bg-blue-700"
            >
              {submitting
                ? t('attendance.manualDay.submitting')
                : t('attendance.manualDay.submit')}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
