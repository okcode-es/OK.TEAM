import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AlertCircle, CalendarPlus, Upload } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type { EmployeeResponse, LeaveType } from '@/api/types'
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
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface AssignLeaveSheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  employees?: EmployeeResponse[]
  lockedEmployee?: EmployeeResponse
  onSuccess?: () => void | Promise<void>
}

const ASSIGN_LEAVE_TYPES: LeaveType[] = [
  'vacation',
  'sick_leave',
  'medical_appointment',
  'day_off',
]

function computeNaturalDays(startDate: string, endDate: string): number {
  if (!startDate || !endDate) return 0
  const start = new Date(`${startDate}T00:00:00Z`)
  const end = new Date(`${endDate}T00:00:00Z`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0
  if (end < start) return 0
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1
}

export function AssignLeaveSheet({
  open,
  onOpenChange,
  employees = [],
  lockedEmployee,
  onSuccess,
}: AssignLeaveSheetProps) {
  const { t, i18n } = useTranslation()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')

  const todayIso = useMemo(
    () =>
      new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Madrid' }).format(
        new Date(),
      ),
    [],
  )

  const [employeeId, setEmployeeId] = useState<string>(lockedEmployee?.id ?? '')
  const [leaveType, setLeaveType] = useState<LeaveType>('vacation')
  const [startDate, setStartDate] = useState<string>(todayIso)
  const [endDate, setEndDate] = useState<string>(todayIso)
  const [reason, setReason] = useState<string>('')
  const [justificanteFile, setJustificanteFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitAttempted, setSubmitAttempted] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setSubmitAttempted(false)
    setApiError(null)
    setJustificanteFile(null)
    setReason('')
    if (lockedEmployee) {
      setEmployeeId(lockedEmployee.id)
    } else if (!employeeId && employees.length > 0) {
      setEmployeeId(employees[0].id)
    }
  }, [open, lockedEmployee, employees, employeeId])

  const missingEmployee = !lockedEmployee?.id && !employeeId
  const missingStartDate = !startDate
  const missingEndDate = !endDate
  const dateOrderError = Boolean(startDate && endDate && endDate < startDate)
  const missingReason = !reason.trim()

  const missingFields: string[] = []
  if (missingEmployee) missingFields.push(t('leaves.createDrawer.validation.fieldEmployee'))
  if (missingStartDate) missingFields.push(t('leaves.createDrawer.validation.fieldStartDate'))
  if (missingEndDate) missingFields.push(t('leaves.createDrawer.validation.fieldEndDate'))
  if (missingReason) missingFields.push(t('leaves.createDrawer.reason'))

  const naturalDays = computeNaturalDays(startDate, endDate)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSubmitAttempted(true)
    setApiError(null)
    if (
      missingEmployee ||
      missingStartDate ||
      missingEndDate ||
      dateOrderError ||
      missingReason
    ) {
      return
    }

    setSubmitting(true)
    try {
      const targetEmployeeId = lockedEmployee?.id ?? employeeId
      const res = await apiClient.POST('/api/v1/leaves/assign', {
        body: {
          employee_id: targetEmployeeId,
          leave_type: leaveType,
          start_date: startDate,
          end_date: endDate,
          reason: reason.trim(),
        },
      })
      if (res.error || !res.data) {
        setApiError(extractApiError(res.error, t('errors.GENERIC')))
        return
      }

      if (
        (leaveType === 'sick_leave' || leaveType === 'medical_appointment') &&
        justificanteFile
      ) {
        const formData = new FormData()
        formData.append('file', justificanteFile)
        const headers = await getAuthHeaders()
        const uploadRes = await fetch(
          `${getApiBaseUrl()}/api/v1/leaves/${res.data.id}/justificante`,
          {
            method: 'POST',
            headers,
            body: formData,
          },
        )
        if (!uploadRes.ok) {
          setApiError(
            await extractFetchError(uploadRes, t('errors.GENERIC')),
          )
          await onSuccess?.()
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
        className="flex w-full flex-col overflow-y-auto bg-white sm:max-w-md"
      >
        <SheetHeader className="border-b border-slate-200 px-6 py-4">
          <SheetTitle className="flex items-center gap-2">
            <CalendarPlus className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
            {isEs ? 'Asignar ausencia' : '为员工安排假期'}
          </SheetTitle>
          <SheetDescription>
            {isEs
              ? 'El registro quedará aprobado directamente y descontará saldo si corresponde.'
              : '由管理员/HR/店长直接为员工排定休假或登记病假，提交后立即生效。'}
          </SheetDescription>
        </SheetHeader>

        <form
          noValidate
          onSubmit={(e) => void handleSubmit(e)}
          className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
        >
          {(missingFields.length > 0 || dateOrderError) && (
            <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <AlertCircle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                aria-hidden="true"
              />
              <div className="space-y-0.5">
                {missingFields.length > 0 && (
                  <div>
                    {isEs
                      ? `Campos pendientes: ${missingFields.join(', ')}`
                      : `请先完善必填项：${missingFields.join('、')}`}
                  </div>
                )}
                {dateOrderError && (
                  <div className="text-rose-700">
                    {isEs
                      ? 'La fecha de fin no puede ser anterior a la de inicio.'
                      : '结束日期不能早于开始日期。'}
                  </div>
                )}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="assign-emp">
              {isEs ? 'Empleado' : '员工'}
              <span className="ml-0.5 text-rose-500" aria-hidden="true">
                *
              </span>
            </Label>
            {lockedEmployee ? (
              <Input
                id="assign-emp"
                disabled
                value={`${lockedEmployee.employee_code} · ${lockedEmployee.full_name}`}
              />
            ) : (
              <Select value={employeeId} onValueChange={setEmployeeId}>
                <SelectTrigger id="assign-emp" className="w-full bg-white">
                  <SelectValue placeholder={isEs ? 'Seleccionar' : '选择员工'} />
                </SelectTrigger>
                <SelectContent>
                  {employees.map((emp) => (
                    <SelectItem key={emp.id} value={emp.id}>
                      {emp.employee_code} · {emp.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="assign-type">{t('leaves.createDrawer.leaveType')}</Label>
            <Select
              value={leaveType}
              onValueChange={(v) => setLeaveType(v as LeaveType)}
            >
              <SelectTrigger id="assign-type" className="w-full bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ASSIGN_LEAVE_TYPES.map((lt) => (
                  <SelectItem key={lt} value={lt}>
                    {t(`leaves.types.${lt}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="assign-start">
                {t('leaves.createDrawer.startDate')}
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              </Label>
              <Input
                id="assign-start"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                aria-invalid={submitAttempted && missingStartDate}
                className={cn(
                  'tabular-nums',
                  submitAttempted && missingStartDate && 'border-rose-400',
                )}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="assign-end">
                {t('leaves.createDrawer.endDate')}
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              </Label>
              <Input
                id="assign-end"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                aria-invalid={
                  submitAttempted && (missingEndDate || dateOrderError)
                }
                className={cn(
                  'tabular-nums',
                  submitAttempted &&
                    (missingEndDate || dateOrderError) &&
                    'border-rose-400',
                )}
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
            <span className="text-slate-600">
              {isEs ? 'Duración estimada' : '预计自然日数'}
            </span>
            <span className="font-semibold text-slate-900 tabular-nums">
              {naturalDays} {isEs ? 'días naturales' : '天'}
            </span>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="assign-reason">
              {t('leaves.createDrawer.reason')}
              <span className="ml-0.5 text-rose-500" aria-hidden="true">
                *
              </span>
            </Label>
            <Input
              id="assign-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
              }}
              placeholder={t('leaves.createDrawer.reasonPlaceholder')}
              aria-invalid={submitAttempted && missingReason}
              className={cn(submitAttempted && missingReason && 'border-rose-400')}
            />
          </div>

          {(leaveType === 'sick_leave' || leaveType === 'medical_appointment') && (
            <div className="space-y-1.5 rounded-lg border border-slate-200 bg-slate-50/70 p-3">
              <Label htmlFor="assign-justificante" className="text-xs">
                {t('leaves.justificante.uploadLabel')}
              </Label>
              <label
                htmlFor="assign-justificante"
                className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-slate-300 bg-white px-3 py-2 text-xs text-slate-600 hover:border-blue-400 hover:text-[#2563EB]"
              >
                <Upload className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                <span className="truncate">
                  {justificanteFile
                    ? justificanteFile.name
                    : t('leaves.justificante.hint')}
                </span>
              </label>
              <input
                id="assign-justificante"
                type="file"
                accept=".pdf,.jpg,.jpeg,.png"
                className="sr-only"
                onChange={(e) =>
                  setJustificanteFile(e.target.files?.[0] ?? null)
                }
              />
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

          <Separator />

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
                ? isEs
                  ? 'Asignando...'
                  : '正在安排...'
                : isEs
                  ? 'Confirmar asignación'
                  : '确认安排假期'}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
