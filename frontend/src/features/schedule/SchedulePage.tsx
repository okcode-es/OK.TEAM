import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Copy,
  Plus,
  Send,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  AlertSeverity,
  EmployeeResponse,
  EmployeeScheduleRowResponse,
  ScheduleAlertResponse,
  ShiftResponse,
  ShiftStatus,
  StoreResponse,
  WeeklyScheduleResponse,
} from '@/api/types'
import { EmployeeCombobox } from '@/components/EmployeeCombobox'
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
import {
  isImeComposing,
  localDateAndTimeInZoneToUtcIso,
  utcIsoToLocalDateAndTime,
} from '@/lib/ime'
import { cn } from '@/lib/utils'

function getMondayIso(date: Date): string {
  const utcDay = date.getUTCDay()
  const diffToMonday = utcDay === 0 ? -6 : 1 - utcDay
  const monday = new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + diffToMonday),
  )
  return monday.toISOString().slice(0, 10)
}

function addDaysIso(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return dt.toISOString().slice(0, 10)
}

function formatDayHeader(
  isoDate: string,
  locale: string,
): { weekday: string; dateLabel: string } {
  const parsed = new Date(`${isoDate}T12:00:00Z`)
  if (Number.isNaN(parsed.getTime())) {
    return { weekday: isoDate, dateLabel: '' }
  }
  const weekday = new Intl.DateTimeFormat(locale, {
    weekday: 'short',
    timeZone: 'UTC',
  }).format(parsed)
  const dateLabel = new Intl.DateTimeFormat(locale, {
    month: '2-digit',
    day: '2-digit',
    timeZone: 'UTC',
  }).format(parsed)
  return { weekday, dateLabel }
}

function formatTimeInZone(
  isoUtc: string,
  locale: string,
  timeZone: string,
): string {
  const parsed = new Date(isoUtc)
  if (Number.isNaN(parsed.getTime())) {
    return isoUtc
  }
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone,
  }).format(parsed)
}

function formatHours(value: string | number, locale: string): string {
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) return String(value)
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 2,
  }).format(num)
}

const ALERT_BADGE_STYLE: Record<AlertSeverity, string> = {
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  violation: 'border-rose-200 bg-rose-50 text-rose-700',
}

const SHIFT_CHIP_STYLE: Record<ShiftStatus, string> = {
  draft: 'border-amber-200 bg-amber-50/80 text-slate-900 hover:bg-amber-100/70',
  published: 'border-blue-200 bg-blue-50/70 text-slate-900 hover:bg-blue-100/60',
  cancelled: 'border-slate-200 bg-slate-100 text-slate-500 line-through',
}

export function SchedulePage() {
  const { activeTenant, profile } = useAuth()
  const scope = `${activeTenant?.tenant_id}:${activeTenant?.role}:${profile?.user_id}`
  return <ScheduleWorkspace key={scope} />
}

function ScheduleWorkspace() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const role = activeTenant?.role
  const canEdit =
    role === 'owner' || role === 'hr' || role === 'store_manager'

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [selectedStoreId, setSelectedStoreId] = useState<string>('')
  const [departmentFilter, setDepartmentFilter] = useState<string>('all')
  const [employeeFilter, setEmployeeFilter] = useState<string>('all')
  const [weekStart, setWeekStart] = useState<string>(() =>
    getMondayIso(new Date()),
  )
  const [schedule, setSchedule] = useState<WeeklyScheduleResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [noticeBanner, setNoticeBanner] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [copyingPrevWeek, setCopyingPrevWeek] = useState(false)

  // Sheet state for create / edit / view
  const [sheetOpen, setSheetOpen] = useState(false)
  const [editingShift, setEditingShift] = useState<ShiftResponse | null>(null)
  const [targetEmployee, setTargetEmployee] =
    useState<EmployeeScheduleRowResponse | null>(null)
  const [targetStoreId, setTargetStoreId] = useState<string>('')
  const [startDate, setStartDate] = useState('')
  const [startTime, setStartTime] = useState('09:00')
  const [endDate, setEndDate] = useState('')
  const [endTime, setEndTime] = useState('17:00')
  const [plannedBreakMinutes, setPlannedBreakMinutes] = useState('30')
  const [shiftStatus, setShiftStatus] = useState<ShiftStatus>('draft')
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)

  const loadStores = useCallback(async () => {
    const [storeRes, empRes] = await Promise.all([
      apiClient.GET('/api/v1/stores'),
      role !== 'employee' ? apiClient.GET('/api/v1/employees') : Promise.resolve(null),
    ])
    if (storeRes.data) {
      setStores(storeRes.data.items)
      setSelectedStoreId((prev) => {
        if (prev && storeRes.data.items.some((s) => s.id === prev)) {
          return prev
        }
        return storeRes.data.items[0]?.id ?? ''
      })
    }
    if (empRes?.data) {
      setEmployees(empRes.data.items)
    }
  }, [role])

  const loadSchedule = useCallback(async () => {
    if (!selectedStoreId && role !== 'employee') {
      setSchedule(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setErrorBanner(null)
    try {
      const res = await apiClient.GET('/api/v1/shifts/weekly', {
        params: {
          query: {
            week_start: weekStart,
            store_id: selectedStoreId || undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        setSchedule(null)
        return
      }
      setSchedule(res.data)
    } finally {
      setLoading(false)
    }
  }, [selectedStoreId, weekStart, role, t])

  useEffect(() => {
    setSelectedStoreId('')
    setSchedule(null)
    setNoticeBanner(null)
    void loadStores()
  }, [loadStores, activeTenant?.tenant_id])

  useEffect(() => {
    void loadSchedule()
  }, [loadSchedule, activeTenant?.tenant_id])

  const storeTimezone = schedule?.store_timezone || 'Europe/Madrid'

  const weekDays = useMemo(() => {
    const base = schedule?.week_start || weekStart
    return Array.from({ length: 7 }, (_, i) => addDaysIso(base, i))
  }, [schedule?.week_start, weekStart])

  const allAlerts = useMemo(() => {
    if (!schedule) return [] as Array<{ employeeName: string; alert: ScheduleAlertResponse }>
    const list: Array<{ employeeName: string; alert: ScheduleAlertResponse }> = []
    for (const row of schedule.rows) {
      for (const alert of row.alerts) {
        list.push({ employeeName: row.full_name, alert })
      }
    }
    return list
  }, [schedule])

  const employeesById = useMemo(() => {
    const map = new Map<string, EmployeeResponse>()
    for (const emp of employees) {
      map.set(emp.id, emp)
    }
    return map
  }, [employees])

  const availableDepartments = useMemo(() => {
    const byKey = new Map<string, string>()
    const rows = schedule?.rows ?? []
    for (const row of rows) {
      const dept = employeesById.get(row.employee_id)?.department?.trim()
      if (!dept) continue
      const key = dept.toLowerCase()
      if (!byKey.has(key)) byKey.set(key, dept)
    }
    if (byKey.size === 0) {
      for (const emp of employees) {
        const dept = emp.department?.trim()
        if (!dept) continue
        const key = dept.toLowerCase()
        if (!byKey.has(key)) byKey.set(key, dept)
      }
    }
    return Array.from(byKey.entries())
      .sort((a, b) => a[1].localeCompare(b[1]))
      .map(([key, label]) => ({ key, label }))
  }, [schedule?.rows, employeesById, employees])

  const filteredRows = useMemo(() => {
    if (!schedule) return []
    return schedule.rows.filter((row) => {
      if (employeeFilter !== 'all' && row.employee_id !== employeeFilter) {
        return false
      }
      if (departmentFilter !== 'all') {
        const dept =
          employeesById.get(row.employee_id)?.department?.trim().toLowerCase() ??
          ''
        if (dept !== departmentFilter) return false
      }
      return true
    })
  }, [schedule, employeeFilter, departmentFilter, employeesById])

  const hasDraftShifts = useMemo(() => {
    if (!schedule) return false
    return schedule.rows.some((row) =>
      row.shifts.some((s) => s.status === 'draft'),
    )
  }, [schedule])

  const openCreateSheet = (row: EmployeeScheduleRowResponse, dayIso: string) => {
    if (!canEdit) return
    const emp = employeesById.get(row.employee_id)
    setEditingShift(null)
    setTargetEmployee(row)
    setTargetStoreId(emp?.primary_store_id ?? selectedStoreId ?? '')
    setStartDate(dayIso)
    setStartTime('09:00')
    setEndDate(dayIso)
    setEndTime('17:00')
    setPlannedBreakMinutes('30')
    setShiftStatus('draft')
    setNotes('')
    setDrawerError(null)
    setSheetOpen(true)
  }

  const openEditSheet = (
    row: EmployeeScheduleRowResponse,
    shift: ShiftResponse,
  ) => {
    setEditingShift(shift)
    setTargetEmployee(row)
    const startLocal = utcIsoToLocalDateAndTime(
      shift.start_at_utc,
      storeTimezone,
    )
    const endLocal = utcIsoToLocalDateAndTime(shift.end_at_utc, storeTimezone)
    setStartDate(startLocal.date)
    setStartTime(startLocal.time)
    setEndDate(endLocal.date)
    setEndTime(endLocal.time)
    setPlannedBreakMinutes(String(shift.planned_break_minutes))
    setShiftStatus(shift.status)
    setNotes(shift.notes)
    setDrawerError(null)
    setSheetOpen(true)
  }

  const handleCopyPreviousWeek = async () => {
    if (!canEdit || !schedule || copyingPrevWeek) return
    setCopyingPrevWeek(true)
    setErrorBanner(null)
    setNoticeBanner(null)
    try {
      const prevWeekStart = addDaysIso(weekStart, -7)
      const prevRes = await apiClient.GET('/api/v1/shifts/weekly', {
        params: {
          query: {
            week_start: prevWeekStart,
            store_id: selectedStoreId || undefined,
          },
        },
      })
      if (prevRes.error || !prevRes.data) {
        setErrorBanner(extractApiError(prevRes.error, t('common.error')))
        return
      }
      const prevTimezone = prevRes.data.store_timezone || storeTimezone
      const shiftsToCopy: ShiftResponse[] = []
      for (const row of prevRes.data.rows) {
        for (const sh of row.shifts) {
          if (sh.status !== 'cancelled') {
            shiftsToCopy.push(sh)
          }
        }
      }
      if (shiftsToCopy.length === 0) {
        setNoticeBanner(t('schedule.copyPrevWeekEmpty'))
        return
      }

      let createdCount = 0
      let skippedCount = 0
      for (const sh of shiftsToCopy) {
        const shiftTargetStoreId = sh.store_id ?? (selectedStoreId || null)
        const targetTz =
          (shiftTargetStoreId
            ? stores.find((s) => s.id === shiftTargetStoreId)?.timezone
            : undefined) ?? storeTimezone
        const sourceTz =
          (shiftTargetStoreId
            ? stores.find((s) => s.id === shiftTargetStoreId)?.timezone
            : undefined) ?? prevTimezone
        const startLocal = utcIsoToLocalDateAndTime(sh.start_at_utc, sourceTz)
        const endLocal = utcIsoToLocalDateAndTime(sh.end_at_utc, sourceTz)
        const nextStartAtUtc = localDateAndTimeInZoneToUtcIso(
          addDaysIso(startLocal.date, 7),
          startLocal.time,
          targetTz,
        )
        const nextEndAtUtc = localDateAndTimeInZoneToUtcIso(
          addDaysIso(endLocal.date, 7),
          endLocal.time,
          targetTz,
        )
        const createRes = await apiClient.POST('/api/v1/shifts', {
          body: {
            employee_id: sh.employee_id,
            start_at_utc: nextStartAtUtc,
            end_at_utc: nextEndAtUtc,
            planned_break_minutes: sh.planned_break_minutes,
            status: 'draft',
            notes: sh.notes,
            store_id: shiftTargetStoreId,
          },
        })
        if (createRes.error || !createRes.data) {
          skippedCount += 1
        } else {
          createdCount += 1
        }
      }

      setNoticeBanner(
        t('schedule.copyPrevWeekSummary', {
          created: createdCount,
          skipped: skippedCount,
        }),
      )
      await loadSchedule()
    } finally {
      setCopyingPrevWeek(false)
    }
  }

  const handlePublishWeek = async () => {
    if (!schedule || !canEdit) return
    setPublishing(true)
    setErrorBanner(null)
    setNoticeBanner(null)
    try {
      const res = await apiClient.POST('/api/v1/shifts/publish-week', {
        body: {
          store_id: schedule.store_id,
          week_start: schedule.week_start,
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        return
      }
      setNoticeBanner(
        res.data.published_count > 0
          ? t('schedule.publishedNotice', { count: res.data.published_count })
          : t('schedule.noDraftsToPublish'),
      )
      await loadSchedule()
    } finally {
      setPublishing(false)
    }
  }

  const handleSaveShift = async (e?: FormEvent) => {
    if (e) e.preventDefault()
    if (!canEdit || !targetEmployee) return

    setSaving(true)
    setDrawerError(null)
    setNoticeBanner(null)

    try {
      const effectiveTimezone =
        (!editingShift && targetStoreId
          ? stores.find((s) => s.id === targetStoreId)?.timezone
          : undefined) ?? storeTimezone
      const startAtUtc = localDateAndTimeInZoneToUtcIso(
        startDate,
        startTime,
        effectiveTimezone,
      )
      const endAtUtc = localDateAndTimeInZoneToUtcIso(
        endDate,
        endTime,
        effectiveTimezone,
      )
      const breakMins = Math.max(0, Number.parseInt(plannedBreakMinutes, 10) || 0)

      if (editingShift) {
        const res = await apiClient.PATCH('/api/v1/shifts/{shift_id}', {
          params: { path: { shift_id: editingShift.id } },
          body: {
            start_at_utc: startAtUtc,
            end_at_utc: endAtUtc,
            planned_break_minutes: breakMins,
            status: shiftStatus,
            notes: notes.trim(),
          },
        })
        if (res.error || !res.data) {
          setDrawerError(extractApiError(res.error, t('common.error')))
          return
        }
      } else {
        const res = await apiClient.POST('/api/v1/shifts', {
          body: {
            employee_id: targetEmployee.employee_id,
            start_at_utc: startAtUtc,
            end_at_utc: endAtUtc,
            planned_break_minutes: breakMins,
            status: shiftStatus === 'cancelled' ? 'draft' : shiftStatus,
            notes: notes.trim(),
            store_id: targetStoreId || null,
          },
        })
        if (res.error || !res.data) {
          setDrawerError(extractApiError(res.error, t('common.error')))
          return
        }
      }

      setSheetOpen(false)
      await loadSchedule()
    } finally {
      setSaving(false)
    }
  }

  const handleCancelShift = async () => {
    if (!canEdit || !editingShift) return
    setCancelling(true)
    setDrawerError(null)
    try {
      const res = await apiClient.DELETE('/api/v1/shifts/{shift_id}', {
        params: { path: { shift_id: editingShift.id } },
      })
      if (res.error) {
        setDrawerError(extractApiError(res.error, t('common.error')))
        return
      }
      setSheetOpen(false)
      await loadSchedule()
    } finally {
      setCancelling(false)
    }
  }

  const handleInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isImeComposing(e)) {
      e.preventDefault()
      void handleSaveShift()
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            {t('schedule.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('schedule.subtitle')}</p>
        </div>
        {!canEdit && (
          <Badge
            variant="outline"
            className="self-start border-slate-200 bg-white text-slate-600"
          >
            {t('schedule.readOnlyHint')}
          </Badge>
        )}
      </div>

      {/* Compact Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white p-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {stores.length > 0 && (
            <Select
              value={selectedStoreId}
              onValueChange={(val) => {
                setSelectedStoreId(val)
                setNoticeBanner(null)
              }}
            >
              <SelectTrigger
                className="min-w-44 bg-white"
                aria-label={t('schedule.selectStore')}
              >
                <SelectValue placeholder={t('schedule.selectStore')} />
              </SelectTrigger>
              <SelectContent>
                {stores.map((store) => (
                  <SelectItem key={store.id} value={store.id}>
                    {store.code} · {store.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          <div className="flex flex-wrap items-center gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setWeekStart((prev) => addDaysIso(prev, -7))}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              {t('schedule.prevWeek')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setWeekStart(getMondayIso(new Date()))}
            >
              {t('schedule.currentWeek')}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setWeekStart((prev) => addDaysIso(prev, 7))}
            >
              {t('schedule.nextWeek')}
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>

          <span className="px-2 text-xs font-medium text-slate-700 tabular-nums">
            {weekDays[0]} — {weekDays[6]}
            {schedule?.store_timezone ? ` · ${schedule.store_timezone}` : ''}
          </span>

          {availableDepartments.length > 0 && (
            <Select
              value={departmentFilter}
              onValueChange={setDepartmentFilter}
            >
              <SelectTrigger
                className="min-w-36 bg-white text-xs"
                aria-label={t('schedule.filterDepartment')}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('schedule.allDepartments')}
                </SelectItem>
                {availableDepartments.map((dept) => (
                  <SelectItem key={dept.key} value={dept.key}>
                    {dept.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {role !== 'employee' && employees.length > 0 && (
            <EmployeeCombobox
              employees={employees}
              value={employeeFilter}
              onValueChange={setEmployeeFilter}
              includeAllOption
              allValue="all"
              allowClear
              ariaLabel={t('schedule.employeeColumn')}
              className="min-w-48"
            />
          )}
        </div>

        {canEdit && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={copyingPrevWeek || loading || !schedule}
              onClick={() => void handleCopyPreviousWeek()}
            >
              <Copy className="h-4 w-4" aria-hidden="true" />
              {copyingPrevWeek
                ? t('schedule.copyingPrevWeek')
                : t('schedule.copyPrevWeek')}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={publishing || !hasDraftShifts || !schedule}
              onClick={() => void handlePublishWeek()}
            >
              <Send className="h-4 w-4" aria-hidden="true" />
              {publishing ? t('schedule.publishing') : t('schedule.publishWeek')}
            </Button>
          </div>
        )}
      </div>

      {/* Banners */}
      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
        >
          {errorBanner}
        </div>
      )}
      {noticeBanner && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {noticeBanner}
        </div>
      )}

      {/* Compliance Alerts Banner */}
      {allAlerts.length > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50/70 p-3">
          <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-900">
            <AlertTriangle className="h-4 w-4 text-amber-700" aria-hidden="true" />
            <span>
              {t('schedule.alertsBannerTitle')} ({allAlerts.length})
            </span>
          </div>
          <ul className="space-y-1 text-xs text-slate-800">
            {allAlerts.map((item, idx) => (
              <li
                key={`${item.alert.employee_id}-${item.alert.code}-${item.alert.local_date}-${idx}`}
                className="flex flex-wrap items-center gap-2"
              >
                <Badge
                  variant="outline"
                  className={ALERT_BADGE_STYLE[item.alert.severity]}
                >
                  {t(`schedule.alertCodes.${item.alert.code}`)}
                </Badge>
                <span className="font-medium text-slate-900">
                  {item.employeeName}
                </span>
                <span className="text-slate-600 tabular-nums">
                  {item.alert.local_date}
                </span>
                {item.alert.actual_value && item.alert.limit_value && (
                  <span className="text-slate-600 tabular-nums">
                    ({item.alert.actual_value} / {item.alert.limit_value})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Template 3: Horizontal Scroll Schedule Grid */}
      {loading ? (
        <div className="space-y-2 rounded-md border border-slate-200 bg-white p-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : !schedule || schedule.rows.length === 0 ? (
        <div className="rounded-md border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
          {stores.length === 0
            ? t('schedule.noStores')
            : t('schedule.emptyRows')}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
          <table className="w-full min-w-[960px] border-collapse text-left text-xs">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50/80 text-slate-600">
                <th className="sticky left-0 z-10 min-w-48 border-r border-slate-200 bg-slate-50 px-3 py-2.5 font-medium">
                  {t('schedule.employeeColumn')}
                </th>
                {weekDays.map((dayIso) => {
                  const { weekday, dateLabel } = formatDayHeader(dayIso, locale)
                  return (
                    <th
                      key={dayIso}
                      className="min-w-28 border-r border-slate-200 px-2.5 py-2.5 font-medium"
                    >
                      <div className="flex items-baseline justify-between gap-1">
                        <span className="text-slate-900">{weekday}</span>
                        <span className="text-[11px] text-slate-500 tabular-nums">
                          {dateLabel}
                        </span>
                      </div>
                    </th>
                  )
                })}
                <th className="min-w-36 px-3 py-2.5 text-right font-medium">
                  {t('schedule.summaryColumn')}
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {filteredRows.map((row) => {
                const isPartTime = row.work_schedule_type === 'part_time'
                return (
                  <tr key={row.employee_id} className="align-top">
                    <td className="sticky left-0 z-10 border-r border-slate-200 bg-white px-3 py-2.5">
                      <div className="font-medium text-slate-900">
                        {row.full_name}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-500">
                        <span className="tabular-nums">{row.employee_code}</span>
                        <Badge
                          variant="outline"
                          className="border-slate-200 bg-slate-50 px-1.5 py-0 text-[10px] text-slate-600"
                        >
                          {isPartTime
                            ? t('schedule.partTimeTag')
                            : t('schedule.fullTimeTag')}
                        </Badge>
                      </div>
                    </td>

                    {weekDays.map((dayIso) => {
                      const dayShifts = row.shifts.filter((s) => {
                        if (s.status === 'cancelled') return false
                        const local = utcIsoToLocalDateAndTime(
                          s.start_at_utc,
                          storeTimezone,
                        )
                        return local.date === dayIso
                      })
                      const dayLeaves = row.approved_leaves.filter(
                        (lv) => lv.start_date <= dayIso && dayIso <= lv.end_date,
                      )

                      return (
                        <td
                          key={dayIso}
                          className="border-r border-slate-200 p-1.5 align-top"
                        >
                          <div className="flex min-h-14 flex-col gap-1">
                            {dayLeaves.map((lv) => (
                              <div
                                key={lv.id}
                                className="rounded border border-amber-200 bg-amber-50 px-1.5 py-1 text-[11px] font-medium text-amber-800"
                              >
                                {t(`leaves.types.${lv.leave_type}`)}
                              </div>
                            ))}

                            {dayShifts.map((shift) => (
                              <button
                                key={shift.id}
                                type="button"
                                onClick={() => openEditSheet(row, shift)}
                                className={cn(
                                  'w-full rounded border px-1.5 py-1 text-left transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
                                  SHIFT_CHIP_STYLE[shift.status],
                                )}
                              >
                                <div className="font-medium tabular-nums">
                                  {formatTimeInZone(
                                    shift.start_at_utc,
                                    locale,
                                    storeTimezone,
                                  )}
                                  {' – '}
                                  {formatTimeInZone(
                                    shift.end_at_utc,
                                    locale,
                                    storeTimezone,
                                  )}
                                </div>
                                <div className="mt-0.5 flex items-center justify-between gap-1 text-[10px] text-slate-600">
                                  <span>
                                    {t(`schedule.statuses.${shift.status}`)}
                                  </span>
                                  <span className="tabular-nums">
                                    {formatHours(shift.net_hours, locale)}h
                                  </span>
                                </div>
                              </button>
                            ))}

                            {canEdit && (
                              <button
                                type="button"
                                onClick={() => openCreateSheet(row, dayIso)}
                                className="mt-auto flex items-center justify-center gap-1 rounded border border-dashed border-slate-200 px-1.5 py-1 text-[11px] text-slate-500 transition-colors hover:border-[#2563EB] hover:bg-slate-50 hover:text-[#2563EB] focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                              >
                                <Plus className="h-3 w-3" aria-hidden="true" />
                                <span>{t('schedule.addShift')}</span>
                              </button>
                            )}
                          </div>
                        </td>
                      )
                    })}

                    <td className="px-3 py-2.5 text-right">
                      <div className="font-medium text-slate-900 tabular-nums">
                        {formatHours(row.weekly_net_hours, locale)}h /{' '}
                        {formatHours(row.contracted_weekly_hours, locale)}h
                      </div>
                      <div className="mt-1 flex justify-end">
                        {row.alerts.length === 0 ? (
                          <Badge
                            variant="outline"
                            className="border-emerald-200 bg-emerald-50 text-emerald-700"
                          >
                            <CheckCircle2
                              className="mr-1 h-3 w-3"
                              aria-hidden="true"
                            />
                            {t('schedule.compliant')}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="border-rose-200 bg-rose-50 text-rose-700 tabular-nums"
                          >
                            <AlertTriangle
                              className="mr-1 h-3 w-3"
                              aria-hidden="true"
                            />
                            {t('schedule.alertsCount', {
                              count: row.alerts.length,
                            })}
                          </Badge>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Right Sheet Editor / Viewer */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
          <SheetHeader>
            <SheetTitle>
              {!canEdit
                ? t('schedule.drawer.viewTitle')
                : editingShift
                  ? t('schedule.drawer.editTitle')
                  : t('schedule.drawer.createTitle')}
            </SheetTitle>
            <SheetDescription>
              {t('schedule.drawer.description', { timezone: storeTimezone })}
            </SheetDescription>
          </SheetHeader>

          <form
            onSubmit={(e) => void handleSaveShift(e)}
            className="mt-4 space-y-4 px-4"
          >
            {drawerError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {drawerError}
              </div>
            )}

            <div className="space-y-1">
              <Label>{t('schedule.drawer.employee')}</Label>
              <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-medium text-slate-900">
                {targetEmployee?.full_name} ({targetEmployee?.employee_code})
              </p>
            </div>

            {!editingShift && stores.length > 0 && (
              <div className="space-y-1.5">
                <Label htmlFor="shift-target-store">
                  {t('schedule.drawer.targetStore')}
                </Label>
                <Select
                  value={targetStoreId}
                  disabled={!canEdit}
                  onValueChange={setTargetStoreId}
                >
                  <SelectTrigger
                    id="shift-target-store"
                    className="w-full bg-white"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {stores.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.code} · {s.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="shift-start-date">
                  {t('schedule.drawer.startDate')}
                </Label>
                <Input
                  id="shift-start-date"
                  type="date"
                  value={startDate}
                  disabled={!canEdit}
                  onChange={(e) => setStartDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="shift-start-time">
                  {t('schedule.drawer.startTime')}
                </Label>
                <Input
                  id="shift-start-time"
                  type="time"
                  value={startTime}
                  disabled={!canEdit}
                  onChange={(e) => setStartTime(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="shift-end-date">
                  {t('schedule.drawer.endDate')}
                </Label>
                <Input
                  id="shift-end-date"
                  type="date"
                  value={endDate}
                  disabled={!canEdit}
                  onChange={(e) => setEndDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="shift-end-time">
                  {t('schedule.drawer.endTime')}
                </Label>
                <Input
                  id="shift-end-time"
                  type="time"
                  value={endTime}
                  disabled={!canEdit}
                  onChange={(e) => setEndTime(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="shift-break">
                  {t('schedule.drawer.plannedBreak')}
                </Label>
                <Input
                  id="shift-break"
                  type="number"
                  min={0}
                  max={240}
                  value={plannedBreakMinutes}
                  disabled={!canEdit}
                  onChange={(e) => setPlannedBreakMinutes(e.target.value)}
                  onKeyDown={handleInputKeyDown}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="shift-status">
                  {t('schedule.drawer.status')}
                </Label>
                <Select
                  value={shiftStatus}
                  disabled={!canEdit}
                  onValueChange={(v) => setShiftStatus(v as ShiftStatus)}
                >
                  <SelectTrigger id="shift-status" className="w-full bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">
                      {t('schedule.statuses.draft')}
                    </SelectItem>
                    <SelectItem value="published">
                      {t('schedule.statuses.published')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="shift-notes">{t('schedule.drawer.notes')}</Label>
              <Input
                id="shift-notes"
                value={notes}
                disabled={!canEdit}
                placeholder={t('schedule.drawer.notesPlaceholder')}
                onChange={(e) => setNotes(e.target.value)}
                onKeyDown={handleInputKeyDown}
              />
            </div>

            {editingShift && (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600 tabular-nums">
                {t('schedule.drawer.netHours')}:{' '}
                <span className="font-semibold text-slate-900">
                  {formatHours(editingShift.net_hours, locale)}h
                </span>
              </div>
            )}

            <Separator />

            <SheetFooter className="flex flex-wrap gap-2 px-0">
              {canEdit ? (
                <>
                  {editingShift && (
                    <Button
                      type="button"
                      variant="outline"
                      disabled={cancelling || saving}
                      className="border-rose-200 text-rose-700 hover:bg-rose-50"
                      onClick={() => void handleCancelShift()}
                    >
                      {cancelling
                        ? t('schedule.drawer.cancelling')
                        : t('schedule.drawer.cancelShift')}
                    </Button>
                  )}
                  <Button type="submit" disabled={saving || cancelling}>
                    {saving
                      ? t('schedule.drawer.saving')
                      : t('schedule.drawer.save')}
                  </Button>
                </>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setSheetOpen(false)}
                >
                  {t('schedule.drawer.close')}
                </Button>
              )}
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  )
}
