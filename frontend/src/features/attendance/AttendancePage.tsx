import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react'
import {
  AlertCircle,
  Calendar,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock,
  Coffee,
  Download,
  FileText,
  History,
  Lock,
  MoreHorizontal,
  PenLine,
  Settings,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type {
  AlertSeverity,
  AttendanceDayReviewResponse,
  AttendanceEventResponse,
  AttendanceSegmentResponse,
  CorrectedEventType,
  DailyAttendanceSummaryResponse,
  EmployeeAttendanceLedgerResponse,
  EmployeeResponse,
  LeaveStatus,
  ShiftResponse,
} from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { BreakPolicyForm } from '@/features/attendance/BreakPolicyForm'
import { ManualDayClockSheet } from '@/features/attendance/ManualDayClockSheet'
import { SegmentedProgressBar } from '@/features/attendance/SegmentedProgressBar'
import { useAuth } from '@/features/auth/AuthContext'
import { useClockState } from '@/features/clock/ClockStateContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface AttendancePageProps {
  embedded?: boolean
}

type ViewMode = 'week' | 'month'

interface LeaveOverlay {
  status: LeaveStatus
  label: string
  isRedacted: boolean
}

interface CalendarDayRow {
  localDate: string
  dayOfWeekIndex: number // 0=Mon .. 6=Sun
  isWeekend: boolean
  isFuture: boolean
  isToday: boolean
  isLocked: boolean
  isSelectable: boolean
  summary: DailyAttendanceSummaryResponse | null
  plannedMinutes: number
  dailyLimitMinutes: number
  effectiveBreakMinutes: number
  leaveOverlay: LeaveOverlay | null
  dayEvents: AttendanceEventResponse[]
  statusKind: 'violation' | 'approved' | 'pending' | 'empty'
}

const CORRECTED_EVENT_TYPES: CorrectedEventType[] = [
  'clock_in',
  'break_start',
  'break_end',
  'clock_out',
  'void',
]

const ALERT_BADGE_CLASS: Record<AlertSeverity, string> = {
  warning: 'border-amber-200 bg-amber-50 text-amber-800',
  violation: 'border-rose-200 bg-rose-50 text-rose-700',
}

function getTodayIsoInZone(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone }).format(new Date())
  } catch {
    return new Date().toISOString().slice(0, 10)
  }
}

function getMondayIso(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d))
  const day = dt.getUTCDay()
  const diff = day === 0 ? -6 : 1 - day
  dt.setUTCDate(dt.getUTCDate() + diff)
  return dt.toISOString().slice(0, 10)
}

function addDaysIso(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return dt.toISOString().slice(0, 10)
}

function addMonthsIso(isoDate: string, deltaMonths: number): string {
  const [y, m] = isoDate.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1 + deltaMonths, 1))
  return dt.toISOString().slice(0, 10)
}

function getMonthRange(isoDate: string): { from: string; to: string } {
  const [y, m] = isoDate.split('-').map(Number)
  const first = new Date(Date.UTC(y, m - 1, 1))
  const last = new Date(Date.UTC(y, m, 0))
  return {
    from: first.toISOString().slice(0, 10),
    to: last.toISOString().slice(0, 10),
  }
}

function enumerateIsoDates(fromIso: string, toIso: string): string[] {
  const result: string[] = []
  let cur = fromIso
  while (cur <= toIso) {
    result.push(cur)
    cur = addDaysIso(cur, 1)
  }
  return result
}

function getDayOfWeekMon0(isoDate: string): number {
  const [y, m, d] = isoDate.split('-').map(Number)
  const utcDay = new Date(Date.UTC(y, m - 1, d)).getUTCDay()
  return (utcDay + 6) % 7
}

function formatCompactHours(minutes: number): string {
  const safe = Math.max(0, Math.round(minutes))
  const h = Math.floor(safe / 60)
  const m = safe % 60
  if (h > 0 && m > 0) return `${h}h ${m}m`
  if (h > 0) return `${h}h`
  if (m > 0) return `${m}m`
  return '0h'
}

function formatTimeHm(
  isoUtc: string | null | undefined,
  locale: string,
  timeZone: string,
): string {
  if (!isoUtc) return '--:--'
  const parsed = new Date(isoUtc)
  if (Number.isNaN(parsed.getTime())) return '--:--'
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone,
  }).format(parsed)
}

function formatPeriodLabel(
  viewMode: ViewMode,
  dateFrom: string,
  dateTo: string,
  locale: string,
): string {
  const start = new Date(`${dateFrom}T12:00:00Z`)
  const end = new Date(`${dateTo}T12:00:00Z`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) {
    return `${dateFrom} – ${dateTo}`
  }
  try {
    if (viewMode === 'month') {
      return new Intl.DateTimeFormat(locale, {
        year: 'numeric',
        month: 'long',
        timeZone: 'UTC',
      }).format(start)
    }
    return new Intl.DateTimeFormat(locale, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      timeZone: 'UTC',
    }).formatRange(start, end)
  } catch {
    return `${dateFrom} – ${dateTo}`
  }
}

function formatRowDate(localDate: string, locale: string): string {
  const dt = new Date(`${localDate}T12:00:00Z`)
  if (Number.isNaN(dt.getTime())) return localDate
  return new Intl.DateTimeFormat(locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  }).format(dt)
}

function formatNumber(value: number, locale: string, maxDecimals = 1): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxDecimals,
  }).format(value)
}

function localDateTimeInZoneToUtcIso(
  localDateTime: string,
  timeZone: string,
): string {
  const [datePart, timePart] = localDateTime.split('T')
  if (!datePart || !timePart) return new Date().toISOString()
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  const naiveUtcMs = Date.UTC(year, month - 1, day, hour, minute, 0)

  const partsFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  const getOffsetMs = (utcMs: number): number => {
    const parts = partsFormatter.formatToParts(new Date(utcMs))
    const map: Record<string, number> = {}
    for (const part of parts) {
      if (part.type !== 'literal') map[part.type] = Number(part.value)
    }
    const asUtc = Date.UTC(
      map.year,
      map.month - 1,
      map.day,
      map.hour,
      map.minute,
      map.second,
    )
    return asUtc - utcMs
  }

  const firstPass = naiveUtcMs - getOffsetMs(naiveUtcMs)
  const secondPass = naiveUtcMs - getOffsetMs(firstPass)
  return new Date(secondPass).toISOString()
}

export function AttendancePage({ embedded = false }: AttendancePageProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const {
    clockVersion,
    hasEmployeeProfile,
    employee: selfEmployee,
    refreshClockState,
  } = useClockState()

  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const role = activeTenant?.role
  const canCorrect =
    role === 'owner' || role === 'hr' || role === 'store_manager'
  const isAccountant = role === 'accountant'
  const canSelfClock = !isAccountant && hasEmployeeProfile
  const canSubmitOrApprove = !isAccountant
  const showEmployeeSelector = role !== 'employee'

  const [viewMode, setViewMode] = useState<ViewMode>('week')
  const [anchorDate, setAnchorDate] = useState<string>(() =>
    getTodayIsoInZone('Europe/Madrid'),
  )

  const { dateFrom, dateTo } = useMemo(() => {
    if (viewMode === 'week') {
      const monday = getMondayIso(anchorDate)
      return { dateFrom: monday, dateTo: addDaysIso(monday, 6) }
    }
    const range = getMonthRange(anchorDate)
    return { dateFrom: range.from, dateTo: range.to }
  }, [viewMode, anchorDate])

  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string>('')
  const [ledger, setLedger] = useState<EmployeeAttendanceLedgerResponse | null>(
    null,
  )
  const [shifts, setShifts] = useState<ShiftResponse[]>([])
  const [leavesByDate, setLeavesByDate] = useState<Map<string, LeaveOverlay>>(
    () => new Map(),
  )

  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [actionFeedback, setActionFeedback] = useState<{
    kind: 'success' | 'error'
    text: string
  } | null>(null)
  const [ledgerRevision, setLedgerRevision] = useState(0)

  // Selection for batch actions
  const [selectedDates, setSelectedDates] = useState<Set<string>>(
    () => new Set(),
  )
  const [batchSubmitting, setBatchSubmitting] = useState(false)

  // Menus & Drawers
  const [moreMenuOpen, setMoreMenuOpen] = useState(false)
  const moreMenuRef = useRef<HTMLDivElement | null>(null)
  const [exportingType, setExportingType] = useState<'csv' | 'pdf' | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [detailsSheetOpen, setDetailsSheetOpen] = useState(false)
  const [historySheetOpen, setHistorySheetOpen] = useState(false)
  const [manualDayOpen, setManualDayOpen] = useState(false)

  // Day detail & append-only correction drawer
  const [selectedDayDate, setSelectedDayDate] = useState<string | null>(null)
  const [correctedType, setCorrectedType] =
    useState<CorrectedEventType>('clock_out')
  const [occurredAtLocal, setOccurredAtLocal] = useState<string>('')
  const [supersedesEventId, setSupersedesEventId] = useState<string>('none')
  const [correctionReason, setCorrectionReason] = useState<string>('')
  const [submittingCorrection, setSubmittingCorrection] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [drawerSuccess, setDrawerSuccess] = useState<string | null>(null)

  // Close More Actions popover on outside click / Escape
  useEffect(() => {
    if (!moreMenuOpen) return
    const handleOutside = (e: MouseEvent) => {
      if (
        moreMenuRef.current &&
        !moreMenuRef.current.contains(e.target as Node)
      ) {
        setMoreMenuOpen(false)
      }
    }
    const handleEsc = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') setMoreMenuOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleEsc)
    return () => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleEsc)
    }
  }, [moreMenuOpen])

  // Load employees list for selector (with AbortController)
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller

    async function loadEmployees() {
      if (role === 'employee') {
        if (selfEmployee?.id) {
          setSelectedEmployeeId(selfEmployee.id)
        }
        return
      }
      try {
        const res = await apiClient.GET('/api/v1/employees', { signal })
        if (signal.aborted) return
        if (res.data) {
          const items = res.data.items
          setEmployees(items)
          setSelectedEmployeeId((prev) => {
            if (prev && items.some((e) => e.id === prev)) return prev
            if (selfEmployee?.id && items.some((e) => e.id === selfEmployee.id)) {
              return selfEmployee.id
            }
            return items[0]?.id ?? ''
          })
        }
      } catch {
        // ignore abort
      }
    }

    setSelectedDates(new Set())
    setSelectedDayDate(null)
    void loadEmployees()
    return () => controller.abort()
  }, [activeTenant?.tenant_id, role, selfEmployee?.id])

  const targetEmployeeId =
    role === 'employee'
      ? (selfEmployee?.id ?? '')
      : selectedEmployeeId || selfEmployee?.id || ''

  const selectedEmployee = useMemo(
    () => employees.find((e) => e.id === targetEmployeeId) ?? null,
    [employees, targetEmployeeId],
  )

  const isSelfTarget = Boolean(
    selfEmployee?.id && targetEmployeeId === selfEmployee.id,
  )

  // Load ledger, shifts, and leaves for active period & target employee
  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller

    async function loadPeriodData() {
      if (!targetEmployeeId && role !== 'employee') {
        setLoading(false)
        return
      }
      setLoading(true)
      setErrorBanner(null)
      try {
        const ledgerPromise = apiClient.GET('/api/v1/attendance/ledger', {
          params: {
            query: {
              employee_id: targetEmployeeId || undefined,
              date_from: dateFrom,
              date_to: dateTo,
            },
          },
          signal,
        })

        // Load shifts for week(s) covered
        const weekStarts: string[] = []
        let ws = getMondayIso(dateFrom)
        while (ws <= dateTo) {
          weekStarts.push(ws)
          ws = addDaysIso(ws, 7)
        }
        const storeIdForShifts = selectedEmployee?.primary_store_id
        const shiftsPromise = Promise.all(
          weekStarts.map((wStart) =>
            apiClient.GET('/api/v1/shifts/weekly', {
              params: {
                query: {
                  week_start: wStart,
                  store_id: storeIdForShifts || undefined,
                },
              },
              signal,
            }),
          ),
        )

        const fromYear = Number(dateFrom.slice(0, 4))
        const leavesPromise = isAccountant
          ? apiClient.GET('/api/v1/leaves/team-calendar', {
              params: {
                query: {
                  from_date: dateFrom,
                  to_date: dateTo,
                },
              },
              signal,
            })
          : apiClient.GET('/api/v1/leaves', {
              params: {
                query: {
                  year: fromYear,
                  employee_id: targetEmployeeId || undefined,
                },
              },
              signal,
            })

        const [ledgerRes, shiftResults, leavesRes] = await Promise.all([
          ledgerPromise,
          shiftsPromise,
          leavesPromise,
        ])

        if (signal.aborted) return

        if (ledgerRes.error || !ledgerRes.data) {
          setErrorBanner(extractApiError(ledgerRes.error, t('errors.GENERIC')))
          setLedger(null)
        } else {
          const matchedLedger =
            ledgerRes.data.items.find(
              (item) => item.employee_id === targetEmployeeId,
            ) ??
            ledgerRes.data.items[0] ??
            null
          setLedger(matchedLedger)
        }

        // Collect shifts for target employee
        const collectedShifts: ShiftResponse[] = []
        for (const sRes of shiftResults) {
          const row = sRes.data?.rows.find(
            (r) => r.employee_id === targetEmployeeId,
          )
          if (row) {
            for (const sh of row.shifts) {
              if (sh.status !== 'cancelled') {
                collectedShifts.push(sh)
              }
            }
          }
        }
        setShifts(collectedShifts)

        // Build leave overlay map by local_date
        const lMap = new Map<string, LeaveOverlay>()
        if (isAccountant && leavesRes.data && 'members' in leavesRes.data) {
          const member = leavesRes.data.members.find(
            (m) => m.employee_id === targetEmployeeId,
          )
          for (const entry of member?.entries ?? []) {
            if (entry.status === 'rejected' || entry.status === 'cancelled') {
              continue
            }
            const isRedacted =
              entry.display_type === 'absent' ||
              entry.display_type === 'absence'
            const label = isRedacted
              ? t('attendance.redesign.redactedAbsence')
              : t(`leaves.types.${entry.display_type}`, {
                  defaultValue: entry.display_type,
                })
            let cur = entry.start_date
            while (cur <= entry.end_date) {
              if (cur >= dateFrom && cur <= dateTo) {
                lMap.set(cur, {
                  status: entry.status,
                  label,
                  isRedacted,
                })
              }
              cur = addDaysIso(cur, 1)
            }
          }
        } else if (leavesRes.data && 'items' in leavesRes.data) {
          for (const lv of leavesRes.data.items) {
            if (lv.employee_id !== targetEmployeeId) continue
            if (lv.status === 'rejected' || lv.status === 'cancelled') continue
            let cur = lv.start_date
            while (cur <= lv.end_date) {
              if (cur >= dateFrom && cur <= dateTo) {
                lMap.set(cur, {
                  status: lv.status,
                  label: t(`leaves.types.${lv.leave_type}`),
                  isRedacted: false,
                })
              }
              cur = addDaysIso(cur, 1)
            }
          }
        }
        setLeavesByDate(lMap)
      } catch (err) {
        if (!signal.aborted) {
          setErrorBanner(extractApiError(err, t('errors.GENERIC')))
        }
      } finally {
        if (!signal.aborted) setLoading(false)
      }
    }

    void loadPeriodData()
    return () => controller.abort()
  }, [
    activeTenant?.tenant_id,
    targetEmployeeId,
    selectedEmployee?.primary_store_id,
    dateFrom,
    dateTo,
    role,
    isAccountant,
    clockVersion,
    ledgerRevision,
    t,
  ])

  // Clear selection when period or employee changes
  useEffect(() => {
    setSelectedDates(new Set())
  }, [dateFrom, dateTo, targetEmployeeId])

  const storeTimeZone = ledger?.store_timezone || 'Europe/Madrid'
  const todayIso = useMemo(
    () => getTodayIsoInZone(storeTimeZone),
    [storeTimeZone],
  )

  const contractedWeeklyMinutes = useMemo(() => {
    const raw =
      ledger?.contracted_weekly_hours ??
      selectedEmployee?.contracted_weekly_hours ??
      '40'
    return Math.round((Number(raw) || 40) * 60)
  }, [ledger?.contracted_weekly_hours, selectedEmployee?.contracted_weekly_hours])

  const defaultDailyMinutes = Math.round(contractedWeeklyMinutes / 5)

  const summaryByDate = useMemo(() => {
    const map = new Map<string, DailyAttendanceSummaryResponse>()
    for (const s of ledger?.daily_summaries ?? []) {
      map.set(s.local_date, s)
    }
    return map
  }, [ledger?.daily_summaries])

  const dayReviewByDate = useMemo(() => {
    const map = new Map<string, AttendanceDayReviewResponse>()
    for (const r of ledger?.day_reviews ?? []) {
      map.set(r.local_date, r)
    }
    return map
  }, [ledger?.day_reviews])

  const shiftsByDate = useMemo(() => {
    const map = new Map<string, ShiftResponse[]>()
    for (const sh of shifts) {
      const localDay = new Intl.DateTimeFormat('en-CA', {
        timeZone: storeTimeZone,
      }).format(new Date(sh.start_at_utc))
      const list = map.get(localDay) ?? []
      list.push(sh)
      map.set(localDay, list)
    }
    return map
  }, [shifts, storeTimeZone])

  const calendarDays = useMemo<CalendarDayRow[]>(() => {
    const dates = enumerateIsoDates(dateFrom, dateTo)
    const closedMonthsSet = new Set(ledger?.closed_months ?? [])
    const isFrozen = selectedEmployee?.employment_status === 'terminated'

    return dates.map((localDate) => {
      const dow = getDayOfWeekMon0(localDate)
      const isWeekend = dow >= 5
      const isFuture = localDate > todayIso
      const isToday = localDate === todayIso
      const summary = summaryByDate.get(localDate) ?? null
      const monthKey = localDate.slice(0, 7)
      const isLocked =
        Boolean(summary?.is_locked) || closedMonthsSet.has(monthKey)

      const dayShifts = shiftsByDate.get(localDate) ?? []
      const shiftPlannedMin = dayShifts.reduce(
        (acc, sh) => acc + Math.round((Number(sh.net_hours) || 0) * 60),
        0,
      )
      const plannedMinutes =
        summary?.planned_minutes ??
        (shiftPlannedMin > 0
          ? shiftPlannedMin
          : isWeekend
            ? 0
            : defaultDailyMinutes)

      const dailyLimitMinutes = summary?.daily_limit_minutes ?? 540
      const effectiveBreakMinutes = summary
        ? summary.total_break_minutes + summary.total_auto_deducted_break_minutes
        : 0

      const hasPunches = Boolean(summary && summary.sessions.length > 0)
      const isSelectable =
        canSubmitOrApprove &&
        !isFuture &&
        !isLocked &&
        !isFrozen &&
        (!isWeekend || hasPunches)

      const dayEvents = (ledger?.events ?? []).filter((ev) => {
        const evLocal = new Intl.DateTimeFormat('en-CA', {
          timeZone: ev.store_timezone || storeTimeZone,
        }).format(new Date(ev.occurred_at_utc))
        return evLocal === localDate
      })

      const hasPastOpenShift =
        !isFuture &&
        !isToday &&
        Boolean(summary?.sessions.some((s) => !s.clock_out_utc))
      const hasViolationAlert = Boolean(
        summary?.alerts.some((a) => a.severity === 'violation'),
      )

      const effectiveReviewStatus =
        summary?.review_status ?? dayReviewByDate.get(localDate)?.status ?? null

      let statusKind: CalendarDayRow['statusKind'] = 'empty'
      if (hasPastOpenShift || hasViolationAlert) {
        statusKind = 'violation'
      } else if (effectiveReviewStatus === 'approved' || isLocked) {
        statusKind = 'approved'
      } else if (
        effectiveReviewStatus === 'submitted' ||
        hasPunches ||
        Boolean(summary && summary.alerts.length > 0)
      ) {
        statusKind = 'pending'
      }

      return {
        localDate,
        dayOfWeekIndex: dow,
        isWeekend,
        isFuture,
        isToday,
        isLocked,
        isSelectable,
        summary,
        plannedMinutes,
        dailyLimitMinutes,
        effectiveBreakMinutes,
        leaveOverlay: leavesByDate.get(localDate) ?? null,
        dayEvents,
        statusKind,
      }
    })
  }, [
    dateFrom,
    dateTo,
    ledger?.closed_months,
    ledger?.events,
    selectedEmployee?.employment_status,
    todayIso,
    summaryByDate,
    dayReviewByDate,
    shiftsByDate,
    defaultDailyMinutes,
    canSubmitOrApprove,
    storeTimeZone,
    leavesByDate,
  ])

  const selectableDates = useMemo(
    () =>
      calendarDays.filter((r) => r.isSelectable).map((r) => r.localDate),
    [calendarDays],
  )

  const allSelectableChecked =
    selectableDates.length > 0 &&
    selectableDates.every((d) => selectedDates.has(d))

  const toggleAllSelectable = () => {
    if (allSelectableChecked) {
      setSelectedDates(new Set())
    } else {
      setSelectedDates(new Set(selectableDates))
    }
  }

  const toggleRowSelection = (localDate: string) => {
    setSelectedDates((prev) => {
      const next = new Set(prev)
      if (next.has(localDate)) {
        next.delete(localDate)
      } else {
        next.add(localDate)
      }
      return next
    })
  }

  // Summary Card 1: Recorded vs Planned
  const totalRecordedMinutes = useMemo(
    () =>
      calendarDays.reduce(
        (acc, d) => acc + (d.summary?.total_net_minutes ?? 0),
        0,
      ),
    [calendarDays],
  )
  const totalPlannedMinutes = useMemo(() => {
    const sumPlanned = calendarDays.reduce(
      (acc, d) => acc + d.plannedMinutes,
      0,
    )
    return sumPlanned > 0 ? sumPlanned : contractedWeeklyMinutes
  }, [calendarDays, contractedWeeklyMinutes])

  // Summary Card 2: Mon-Sun Schedule Strip
  const scheduleStripDays = useMemo(() => {
    const monday = getMondayIso(anchorDate)
    const shortLabels = isEs
      ? ['L', 'M', 'X', 'J', 'V', 'S', 'D']
      : ['一', '二', '三', '四', '五', '六', '日']

    return Array.from({ length: 7 }, (_, idx) => {
      const iso = addDaysIso(monday, idx)
      const dayShifts = shiftsByDate.get(iso) ?? []
      const shiftMin = dayShifts.reduce(
        (acc, sh) => acc + Math.round((Number(sh.net_hours) || 0) * 60),
        0,
      )
      const summaryPlanned = summaryByDate.get(iso)?.planned_minutes
      const planned =
        summaryPlanned ??
        (shiftMin > 0 ? shiftMin : idx < 5 ? defaultDailyMinutes : 0)
      return {
        iso,
        label: shortLabels[idx],
        plannedMinutes: planned,
        isToday: iso === todayIso,
      }
    })
  }, [anchorDate, isEs, shiftsByDate, summaryByDate, defaultDailyMinutes, todayIso])

  const handlePeriodNav = (direction: -1 | 1) => {
    setAnchorDate((prev) =>
      viewMode === 'week'
        ? addDaysIso(prev, direction * 7)
        : addMonthsIso(prev, direction),
    )
  }

  const handleToday = () => {
    setAnchorDate(todayIso)
  }

  const handleExport = async (kind: 'csv' | 'pdf') => {
    setMoreMenuOpen(false)
    setExportingType(kind)
    setActionFeedback(null)
    try {
      const params = new URLSearchParams()
      if (targetEmployeeId) params.set('employee_id', targetEmployeeId)
      if (dateFrom) params.set('date_from', dateFrom)
      if (dateTo) params.set('date_to', dateTo)
      const qs = params.toString()
      const endpoint =
        kind === 'pdf'
          ? '/api/v1/attendance/export.pdf'
          : '/api/v1/attendance/export.csv'
      const url = `${getApiBaseUrl()}${endpoint}${qs ? `?${qs}` : ''}`
      const headers = await getAuthHeaders()
      const response = await fetch(url, { headers })
      if (!response.ok) {
        setActionFeedback({
          kind: 'error',
          text: await extractFetchError(response, t('errors.GENERIC')),
        })
        return
      }
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download =
        kind === 'pdf'
          ? `registro-jornada-${dateFrom}_${dateTo}.pdf`
          : `registro-jornada-itss-${dateFrom}_${dateTo}.csv`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(objectUrl)
    } catch (err) {
      setActionFeedback({
        kind: 'error',
        text: extractApiError(err, t('errors.GENERIC')),
      })
    } finally {
      setExportingType(null)
    }
  }

  const handleBatchReview = async (action: 'submit' | 'approve') => {
    if (!targetEmployeeId || selectedDates.size === 0) return
    setBatchSubmitting(true)
    setActionFeedback(null)
    try {
      const res = await apiClient.POST('/api/v1/attendance/day-reviews', {
        body: {
          employee_id: targetEmployeeId,
          dates: Array.from(selectedDates).sort(),
          action,
          note: '',
        },
      })
      if (res.error || !res.data) {
        setActionFeedback({
          kind: 'error',
          text: extractApiError(res.error, t('errors.GENERIC')),
        })
        return
      }
      setActionFeedback({
        kind: 'success',
        text:
          action === 'approve'
            ? t('attendance.redesign.batchApprovedNotice', {
                count: selectedDates.size,
              })
            : t('attendance.redesign.batchSubmittedNotice', {
                count: selectedDates.size,
              }),
      })
      setSelectedDates(new Set())
      setLedgerRevision((v) => v + 1)
    } catch (err) {
      setActionFeedback({
        kind: 'error',
        text: extractApiError(err, t('errors.GENERIC')),
      })
    } finally {
      setBatchSubmitting(false)
    }
  }

  const selectedDayRow = useMemo(
    () =>
      selectedDayDate
        ? calendarDays.find((d) => d.localDate === selectedDayDate) ?? null
        : null,
    [calendarDays, selectedDayDate],
  )

  const openDayDetail = (row: CalendarDayRow) => {
    if (
      row.dayEvents.length === 0 &&
      (!row.summary || row.summary.sessions.length === 0) &&
      (canSelfClock || canCorrect) &&
      !row.isLocked &&
      !row.isFuture
    ) {
      setManualDayOpen(true)
      return
    }
    setSelectedDayDate(row.localDate)
    setCorrectedType('clock_out')
    setOccurredAtLocal(`${row.localDate}T18:00`)
    setSupersedesEventId('none')
    setCorrectionReason('')
    setDrawerError(null)
    setDrawerSuccess(null)
  }

  const handleCorrectionSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!selectedDayRow || !canCorrect || !targetEmployeeId) return

    const trimmedReason = correctionReason.trim()
    if (!trimmedReason) {
      setDrawerError(t('attendance.drawer.reasonRequired'))
      return
    }

    const storeId =
      selectedDayRow.summary?.store_id ??
      ledger?.store_id ??
      selectedEmployee?.primary_store_id
    if (!storeId) return

    setSubmittingCorrection(true)
    setDrawerError(null)
    setDrawerSuccess(null)
    try {
      const occurredAtUtc = localDateTimeInZoneToUtcIso(
        occurredAtLocal,
        storeTimeZone,
      )
      const res = await apiClient.POST('/api/v1/attendance/corrections', {
        body: {
          employee_id: targetEmployeeId,
          store_id: storeId,
          corrected_event_type: correctedType,
          occurred_at_utc: occurredAtUtc,
          supersedes_event_id:
            supersedesEventId !== 'none' ? supersedesEventId : null,
          correction_reason: trimmedReason,
        },
      })
      if (res.error || !res.data) {
        setDrawerError(extractApiError(res.error, t('errors.GENERIC')))
        return
      }
      setCorrectionReason('')
      setSupersedesEventId('none')
      setDrawerSuccess(t('attendance.drawer.savedNotice'))
      await refreshClockState()
      setLedgerRevision((v) => v + 1)
    } catch (err) {
      setDrawerError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setSubmittingCorrection(false)
    }
  }

  const periodLabel = useMemo(
    () => formatPeriodLabel(viewMode, dateFrom, dateTo, locale),
    [viewMode, dateFrom, dateTo, locale],
  )

  return (
    <div className="space-y-4">
      {!embedded && (
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            {isEs ? 'Gestión del tiempo' : '时间管理'}
          </h1>
          <p className="text-sm text-slate-600">{t('attendance.subtitle')}</p>
        </div>
      )}

      {/* Date & View Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/75 bg-white px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleToday}
            className="h-8 border-slate-200 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50"
          >
            {isEs ? 'Hoy' : '今天'}
          </Button>
          <div className="inline-flex items-center rounded-lg border border-slate-200 bg-white">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => handlePeriodNav(-1)}
              aria-label={t('attendance.redesign.prevPeriod')}
              className="h-8 w-8 rounded-r-none"
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => handlePeriodNav(1)}
              aria-label={t('attendance.redesign.nextPeriod')}
              className="h-8 w-8 rounded-l-none border-l border-slate-200"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
          <span className="px-1 text-sm font-semibold text-slate-900 tabular-nums">
            {periodLabel}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {showEmployeeSelector && employees.length > 0 && (
            <Select
              value={targetEmployeeId}
              onValueChange={(val) => setSelectedEmployeeId(val)}
            >
              <SelectTrigger
                aria-label={t('attendance.filters.employeeLabel')}
                className="h-8 min-w-44 border-slate-200 bg-white text-xs"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {employees.map((emp) => (
                  <SelectItem key={emp.id} value={emp.id}>
                    {emp.full_name} ({emp.employee_code})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {/* Week / Month segmented toggle */}
          <div
            role="group"
            aria-label={t('attendance.redesign.viewModeLabel')}
            className="inline-flex rounded-lg border border-slate-200 bg-slate-100/80 p-0.5"
          >
            <button
              type="button"
              onClick={() => setViewMode('week')}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                viewMode === 'week'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {t('attendance.redesign.weekView')}
            </button>
            <button
              type="button"
              onClick={() => setViewMode('month')}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                viewMode === 'month'
                  ? 'bg-white text-slate-900 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {t('attendance.redesign.monthView')}
            </button>
          </div>

          {/* Settings button */}
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            onClick={() => setSettingsOpen(true)}
            aria-label={t('attendance.redesign.settingsTitle')}
            title={t('attendance.redesign.settingsTitle')}
            className="h-8 w-8 border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900"
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
          </Button>

          {/* More Actions menu */}
          <div ref={moreMenuRef} className="relative">
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              aria-haspopup="menu"
              aria-expanded={moreMenuOpen}
              aria-label={t('attendance.redesign.moreActions')}
              title={t('attendance.redesign.moreActions')}
              onClick={() => setMoreMenuOpen((prev) => !prev)}
              className="h-8 w-8 border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900"
            >
              <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
            </Button>

            {moreMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 z-30 mt-1.5 w-60 rounded-xl border border-slate-200 bg-white p-1.5 shadow-md"
              >
                <button
                  type="button"
                  role="menuitem"
                  disabled={exportingType !== null}
                  onClick={() => void handleExport('pdf')}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
                >
                  <FileText className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
                  <span>
                    {exportingType === 'pdf'
                      ? t('attendance.exporting')
                      : t('attendance.redesign.exportPdf')}
                  </span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  disabled={exportingType !== null}
                  onClick={() => void handleExport('csv')}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100 disabled:opacity-50"
                >
                  <Download className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                  <span>
                    {exportingType === 'csv'
                      ? t('attendance.exporting')
                      : t('attendance.redesign.exportCsv')}
                  </span>
                </button>
                {(canSelfClock || canCorrect) && (
                  <>
                    <Separator className="my-1" />
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        setMoreMenuOpen(false)
                        setManualDayOpen(true)
                      }}
                      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-slate-700 transition-colors hover:bg-slate-100"
                    >
                      <PenLine className="h-3.5 w-3.5 text-slate-500" aria-hidden="true" />
                      <span>{t('attendance.redesign.manualEntry')}</span>
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {errorBanner && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{errorBanner}</span>
        </div>
      )}

      {actionFeedback && (
        <div
          role={actionFeedback.kind === 'error' ? 'alert' : 'status'}
          className={cn(
            'flex items-center justify-between gap-2 rounded-xl border px-4 py-2.5 text-xs font-medium',
            actionFeedback.kind === 'error'
              ? 'border-rose-200 bg-rose-50 text-rose-700'
              : 'border-emerald-200 bg-emerald-50 text-emerald-700',
          )}
        >
          <span>{actionFeedback.text}</span>
          <button
            type="button"
            onClick={() => setActionFeedback(null)}
            className="text-xs underline opacity-80 hover:opacity-100"
          >
            {t('common.close')}
          </button>
        </div>
      )}

      {/* Side-by-Side Dual Summary Cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left Card: Recorded Hours */}
        <section className="rounded-xl border border-slate-200/75 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-900">
              {t('attendance.redesign.recordedHoursTitle')}
            </h2>
            <button
              type="button"
              onClick={() => setDetailsSheetOpen(true)}
              className="text-xs font-medium text-[#2563EB] hover:text-blue-700 hover:underline focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
            >
              {t('attendance.redesign.viewDetails')}
            </button>
          </div>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-baseline gap-1.5">
              <span className="text-2xl font-bold text-slate-900 tabular-nums sm:text-3xl">
                {formatCompactHours(totalRecordedMinutes)}
              </span>
              <span className="text-xl font-medium text-slate-400 tabular-nums">
                / {formatCompactHours(totalPlannedMinutes)}
              </span>
            </div>

            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-[#2563EB] tabular-nums">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              {formatCompactHours(totalRecordedMinutes)}
            </span>
          </div>

          <p className="mt-2 text-xs text-slate-600">
            {viewMode === 'week'
              ? t('attendance.redesign.recordedThisWeek')
              : t('attendance.redesign.recordedThisMonth')}
          </p>
        </section>

        {/* Right Card: Standard Schedule */}
        <section className="rounded-xl border border-slate-200/75 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <h2 className="text-sm font-semibold text-slate-900">
                {t('attendance.redesign.workScheduleTitle')}
              </h2>
              <span className="text-xs font-medium text-slate-600 tabular-nums">
                {formatNumber(contractedWeeklyMinutes / 60, locale)}h/
                {t('attendance.redesign.perWeekUnit')}
              </span>
            </div>
            <button
              type="button"
              onClick={() => setHistorySheetOpen(true)}
              className="text-xs font-medium text-[#2563EB] hover:text-blue-700 hover:underline focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
            >
              {t('attendance.redesign.viewHistory')}
            </button>
          </div>

          <div className="mt-4 grid grid-cols-7 gap-1.5">
            {scheduleStripDays.map((day) => (
              <div
                key={day.iso}
                className={cn(
                  'flex flex-col items-center justify-center rounded-lg border px-1.5 py-2 text-center',
                  day.isToday
                    ? 'border-[#2563EB] bg-blue-50/50 text-[#2563EB]'
                    : 'border-slate-200/80 bg-slate-50/60 text-slate-700',
                )}
              >
                <span className="text-[11px] font-medium text-slate-600">
                  {day.label}
                </span>
                <span className="mt-1 text-xs font-semibold tabular-nums">
                  {day.plannedMinutes > 0
                    ? formatCompactHours(day.plannedMinutes)
                    : '—'}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* High-Density Daily Attendance Table */}
      <section className="overflow-hidden rounded-xl border border-slate-200/75 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <span className="text-xs text-slate-600 tabular-nums">
            {t('attendance.redesign.periodDayCount', {
              count: calendarDays.length,
            })}
          </span>

          <div className="flex flex-wrap items-center gap-3 text-xs text-slate-600">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-blue-600" aria-hidden="true" />
              {t('attendance.redesign.legendWork')}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-amber-400" aria-hidden="true" />
              {t('attendance.redesign.legendBreak')}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full bg-rose-500" aria-hidden="true" />
              {t('attendance.redesign.legendOvertime')}
            </span>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-label={t('attendance.tableHeading')}
          className="overflow-x-auto"
        >
          <Table>
            <TableHeader>
              <TableRow className="border-slate-100 bg-slate-50/70 hover:bg-slate-50/70">
                <TableHead className="w-10 px-3">
                  {canSubmitOrApprove && (
                    <Checkbox
                      checked={allSelectableChecked}
                      onCheckedChange={toggleAllSelectable}
                      disabled={selectableDates.length === 0}
                      aria-label={t('attendance.redesign.selectAllDays')}
                    />
                  )}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colDate')}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colStartEnd')}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colRecorded')}
                </TableHead>
                <TableHead className="min-w-44 whitespace-nowrap">
                  {t('attendance.redesign.colProgress')}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colBreaks')}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colAbsence')}
                </TableHead>
                <TableHead className="whitespace-nowrap">
                  {t('attendance.redesign.colStatus')}
                </TableHead>
                <TableHead className="w-12 text-right whitespace-nowrap">
                  <span className="sr-only">
                    {t('attendance.redesign.colActions')}
                  </span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                Array.from({ length: viewMode === 'week' ? 7 : 10 }).map(
                  (_, idx) => (
                    <TableRow key={idx}>
                      <TableCell colSpan={9} className="px-4 py-3">
                        <Skeleton className="h-5 w-full" />
                      </TableCell>
                    </TableRow>
                  ),
                )
              ) : (
                calendarDays.map((row) => {
                  const sessions = row.summary?.sessions ?? []
                  const firstSession = sessions[0]
                  const lastSession = sessions[sessions.length - 1]
                  const isMutedRow =
                    row.isLocked ||
                    row.isFuture ||
                    (row.isWeekend && sessions.length === 0)

                  return (
                    <TableRow
                      key={row.localDate}
                      className={cn(
                        'border-slate-100 transition-colors',
                        row.isToday && 'bg-blue-50/50 hover:bg-blue-50/70',
                        !row.isToday && isMutedRow && 'bg-slate-50/50 text-slate-500',
                        !row.isToday && !isMutedRow && 'hover:bg-slate-50/70',
                      )}
                    >
                      {/* 1. Checkbox / Lock */}
                      <TableCell className="w-10 px-3">
                        {row.isSelectable ? (
                          <Checkbox
                            checked={selectedDates.has(row.localDate)}
                            onCheckedChange={() =>
                              toggleRowSelection(row.localDate)
                            }
                            aria-label={row.localDate}
                          />
                        ) : (
                          <Lock
                            className="h-3.5 w-3.5 text-slate-400"
                            aria-label={t('attendance.redesign.lockedDay')}
                          />
                        )}
                      </TableCell>

                      {/* 2. Date */}
                      <TableCell
                        className={cn(
                          'text-xs whitespace-nowrap tabular-nums',
                          row.isToday
                            ? 'font-semibold text-[#2563EB]'
                            : 'font-medium text-slate-900',
                        )}
                      >
                        {formatRowDate(row.localDate, locale)}
                      </TableCell>

                      {/* 3. Start -> End */}
                      <TableCell className="text-xs whitespace-nowrap text-slate-700 tabular-nums">
                        {sessions.length === 0 ? (
                          <span className="text-slate-400">—</span>
                        ) : (
                          <div className="inline-flex items-center gap-1.5">
                            <span>
                              {formatTimeHm(
                                firstSession?.clock_in_utc,
                                locale,
                                storeTimeZone,
                              )}
                              {' → '}
                              {lastSession?.clock_out_utc
                                ? formatTimeHm(
                                    lastSession.clock_out_utc,
                                    locale,
                                    storeTimeZone,
                                  )
                                : lastSession?.has_open_break
                                  ? t('attendance.onBreak')
                                  : t('attendance.inProgress')}
                            </span>
                            {sessions.length >= 2 && (
                              <button
                                type="button"
                                onClick={() => openDayDetail(row)}
                                className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-[#2563EB] hover:bg-blue-50"
                              >
                                {t('attendance.redesign.segmentsCountBadge', {
                                  count: sessions.length,
                                })}
                              </button>
                            )}
                          </div>
                        )}
                      </TableCell>

                      {/* 4. Recorded hours */}
                      <TableCell className="text-xs whitespace-nowrap tabular-nums">
                        {row.summary && row.summary.total_net_minutes > 0 ? (
                          <span>
                            <span className="font-semibold text-slate-900">
                              {formatCompactHours(row.summary.total_net_minutes)}
                            </span>
                            <span className="text-slate-400">
                              {' '}
                              / {formatCompactHours(row.plannedMinutes)}
                            </span>
                          </span>
                        ) : (
                          <span className="text-slate-400">
                            0h / {formatCompactHours(row.plannedMinutes)}
                          </span>
                        )}
                      </TableCell>

                      {/* 5. Segmented Progress Bar */}
                      <TableCell className="min-w-44">
                        <SegmentedProgressBar
                          segments={row.summary?.segments ?? []}
                          plannedMinutes={row.plannedMinutes}
                          dailyLimitMinutes={row.dailyLimitMinutes}
                          timeZone={storeTimeZone}
                          locale={locale}
                        />
                      </TableCell>

                      {/* 6. Breaks */}
                      <TableCell className="text-xs whitespace-nowrap text-slate-700 tabular-nums">
                        {row.effectiveBreakMinutes > 0
                          ? formatCompactHours(row.effectiveBreakMinutes)
                          : '—'}
                      </TableCell>

                      {/* 7. Absence / Leave */}
                      <TableCell className="text-xs whitespace-nowrap">
                        {row.leaveOverlay ? (
                          <Badge
                            variant="outline"
                            className="border-sky-200 bg-sky-50 text-sky-700"
                          >
                            {row.leaveOverlay.label}
                          </Badge>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>

                      {/* 8. Approval Status */}
                      <TableCell className="text-xs whitespace-nowrap">
                        {row.statusKind === 'violation' && (
                          <Badge
                            variant="outline"
                            className="border-rose-200 bg-rose-50 text-rose-700"
                          >
                            {t('attendance.redesign.statusViolation')}
                          </Badge>
                        )}
                        {row.statusKind === 'approved' && (
                          <Badge
                            variant="outline"
                            className="border-emerald-200 bg-emerald-50 text-emerald-700"
                          >
                            {t('attendance.redesign.statusApproved')}
                          </Badge>
                        )}
                        {row.statusKind === 'pending' && (
                          <Badge
                            variant="outline"
                            className="border-amber-200 bg-amber-50 text-amber-800"
                          >
                            {t('attendance.redesign.statusPending')}
                          </Badge>
                        )}
                        {row.statusKind === 'empty' && (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>

                      {/* 9. Row Actions */}
                      <TableCell className="w-12 text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => openDayDetail(row)}
                          aria-label={`${t('attendance.redesign.colActions')} ${row.localDate}`}
                          className="h-7 w-7 text-slate-500 hover:text-slate-900"
                        >
                          <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      {/* Bottom Floating Batch Action Bar */}
      {selectedDates.size > 0 && (
        <div className="sticky bottom-4 z-10 mx-auto mt-3 flex w-fit flex-wrap items-center gap-3 rounded-xl border border-slate-800 bg-[#0B1120] px-4 py-2.5 text-xs text-white shadow-md">
          <span className="font-medium tabular-nums">
            {t('attendance.redesign.selectedDaysCount', {
              count: selectedDates.size,
            })}
          </span>

          {isSelfTarget && (
            <Button
              type="button"
              size="sm"
              disabled={batchSubmitting}
              onClick={() => void handleBatchReview('submit')}
              className="h-7 bg-slate-800 text-xs text-white hover:bg-slate-700"
            >
              {t('attendance.redesign.batchSubmit')}
            </Button>
          )}

          {canCorrect && (
            <Button
              type="button"
              size="sm"
              disabled={batchSubmitting}
              onClick={() => void handleBatchReview('approve')}
              className="h-7 bg-[#2563EB] text-xs text-white hover:bg-blue-700"
            >
              <Check className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              {t('attendance.redesign.batchApprove')}
            </Button>
          )}

          <button
            type="button"
            disabled={batchSubmitting}
            onClick={() => setSelectedDates(new Set())}
            className="text-xs text-slate-300 underline hover:text-white"
          >
            {t('attendance.redesign.clearSelection')}
          </button>
        </div>
      )}

      {/* Settings Sheet: BreakPolicyForm + Manual Clock Trigger */}
      <Sheet open={settingsOpen} onOpenChange={setSettingsOpen}>
        <SheetContent className="w-full overflow-y-auto overscroll-contain bg-white sm:max-w-lg">
          <SheetHeader className="border-b border-slate-200 pb-4">
            <SheetTitle className="flex items-center gap-2">
              <Coffee className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
              {t('attendance.redesign.settingsTitle')}
            </SheetTitle>
            <SheetDescription>
              {t('attendance.redesign.settingsDescription')}
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-5 px-4 py-4">
            {targetEmployeeId && (
              <BreakPolicyForm
                key={`${activeTenant?.tenant_id}:${role}:${targetEmployeeId}`}
                scopeType="employee"
                employeeId={targetEmployeeId}
                readOnly={!canCorrect}
                onSaved={() => setLedgerRevision((v) => v + 1)}
              />
            )}

            {(canSelfClock || canCorrect) && (
              <>
                <Separator />
                <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
                  <div className="text-xs text-slate-600">
                    {t('attendance.manualDay.description')}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setSettingsOpen(false)
                      setManualDayOpen(true)
                    }}
                    className="shrink-0"
                  >
                    {t('attendance.redesign.manualEntry')}
                  </Button>
                </div>
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* Period Breakdown Sheet ("Ver detalles") */}
      <Sheet open={detailsSheetOpen} onOpenChange={setDetailsSheetOpen}>
        <SheetContent className="w-full overflow-y-auto overscroll-contain bg-white sm:max-w-xl">
          <SheetHeader className="border-b border-slate-200 pb-4">
            <SheetTitle>{t('attendance.redesign.periodBreakdownTitle')}</SheetTitle>
            <SheetDescription className="tabular-nums">
              {periodLabel}
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-4 px-4 py-4">
            {calendarDays.filter((d) => d.summary && d.summary.segments.length > 0)
              .length === 0 ? (
              <p className="text-xs text-slate-600">{t('attendance.empty')}</p>
            ) : (
              calendarDays
                .filter((d) => d.summary && d.summary.segments.length > 0)
                .map((d) => (
                  <div
                    key={d.localDate}
                    className="space-y-2 rounded-lg border border-slate-200 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-900 tabular-nums">
                        {formatRowDate(d.localDate, locale)}
                      </span>
                      <span className="font-semibold text-[#2563EB] tabular-nums">
                        {formatCompactHours(d.summary?.total_net_minutes ?? 0)}
                      </span>
                    </div>

                    <div className="space-y-1">
                      {(d.summary?.segments ?? []).map(
                        (seg: AttendanceSegmentResponse, idx: number) => (
                          <div
                            key={`${d.localDate}-seg-${idx}`}
                            className="flex items-center justify-between text-slate-600 tabular-nums"
                          >
                            <span>
                              {seg.type === 'work'
                                ? t('attendance.redesign.legendWork')
                                : `${t('attendance.redesign.legendBreak')} (${seg.is_paid ? t('attendance.redesign.paid') : t('attendance.redesign.unpaid')})`}
                            </span>
                            <span>
                              {formatTimeHm(seg.start_at, locale, storeTimeZone)}
                              {' → '}
                              {seg.end_at
                                ? formatTimeHm(seg.end_at, locale, storeTimeZone)
                                : t('attendance.inProgress')}
                              {' · '}
                              {formatCompactHours(seg.duration_minutes)}
                            </span>
                          </div>
                        ),
                      )}
                    </div>

                    {(d.summary?.total_auto_deducted_break_minutes ?? 0) > 0 && (
                      <div className="text-[11px] text-amber-800 tabular-nums">
                        {t('attendance.redesign.autoDeductedBreakNote', {
                          minutes: d.summary?.total_auto_deducted_break_minutes,
                        })}
                      </div>
                    )}

                    {(d.summary?.alerts ?? []).length > 0 && (
                      <div className="flex flex-wrap gap-1.5 pt-1">
                        {d.summary?.alerts.map((al) => (
                          <Badge
                            key={`${d.localDate}-${al.code}`}
                            variant="outline"
                            className={ALERT_BADGE_CLASS[al.severity]}
                          >
                            {t(`attendance.alertCodes.${al.code}`, {
                              defaultValue: al.message,
                            })}
                          </Badge>
                        ))}
                      </div>
                    )}
                  </div>
                ))
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* Schedule & Break Policy History Sheet ("Ver historial") */}
      <Sheet open={historySheetOpen} onOpenChange={setHistorySheetOpen}>
        <SheetContent className="w-full overflow-y-auto overscroll-contain bg-white sm:max-w-lg">
          <SheetHeader className="border-b border-slate-200 pb-4">
            <SheetTitle className="flex items-center gap-2">
              <History className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
              {t('attendance.redesign.historyTitle')}
            </SheetTitle>
            <SheetDescription className="tabular-nums">
              {periodLabel}
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-5 px-4 py-4">
            <div className="space-y-2">
              <h3 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                {t('attendance.redesign.effectiveBreakPolicy')}
              </h3>
              {ledger?.effective_break_policy ? (
                <div className="space-y-1.5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-600">
                      {t('attendance.redesign.inheritanceSource')}
                    </span>
                    <span className="font-semibold text-slate-900">
                      {t(
                        `attendance.redesign.inheritedFrom.${ledger.effective_break_policy.inherited_from}`,
                      )}
                    </span>
                  </div>
                  <div className="flex justify-between gap-2 tabular-nums">
                    <span className="text-slate-600">
                      {t('attendance.redesign.breakDuration')}
                    </span>
                    <span className="font-medium text-slate-900">
                      {ledger.effective_break_policy.effective_duration_minutes ??
                        ledger.effective_break_policy.duration_minutes ??
                        0}{' '}
                      min
                    </span>
                  </div>
                  <div className="flex justify-between gap-2">
                    <span className="text-slate-600">
                      {t('attendance.redesign.breakPaidStatus')}
                    </span>
                    <span className="font-medium text-slate-900">
                      {ledger.effective_break_policy.is_paid
                        ? t('attendance.redesign.paid')
                        : t('attendance.redesign.unpaid')}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-slate-600">—</p>
              )}
            </div>

            <Separator />

            <div className="space-y-2">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                <Calendar className="h-3.5 w-3.5" aria-hidden="true" />
                {t('attendance.redesign.publishedShifts')}
              </h3>
              {shifts.length === 0 ? (
                <p className="text-xs text-slate-600">
                  {t('attendance.redesign.noShiftsInPeriod')}
                </p>
              ) : (
                <div className="space-y-2">
                  {shifts.map((sh) => (
                    <div
                      key={sh.id}
                      className="flex items-center justify-between rounded-lg border border-slate-200 p-2.5 text-xs tabular-nums"
                    >
                      <div>
                        <div className="font-medium text-slate-900">
                          {formatUtcDateTime(
                            sh.start_at_utc,
                            locale,
                            storeTimeZone,
                          )}
                          {' → '}
                          {formatTimeHm(sh.end_at_utc, locale, storeTimeZone)}
                        </div>
                        {sh.notes && (
                          <div className="text-[11px] text-slate-600">
                            {sh.notes}
                          </div>
                        )}
                      </div>
                      <Badge variant="outline" className="border-slate-200">
                        {formatCompactHours(
                          Math.round((Number(sh.net_hours) || 0) * 60),
                        )}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>

      {/* Day Detail & Append-Only Correction Sheet */}
      <Sheet
        open={selectedDayRow !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedDayDate(null)
        }}
      >
        <SheetContent className="w-full overflow-y-auto overscroll-contain bg-white sm:max-w-xl">
          {selectedDayRow && (
            <>
              <SheetHeader className="border-b border-slate-200 pb-4">
                <SheetTitle>{t('attendance.drawer.title')}</SheetTitle>
                <SheetDescription>
                  {t('attendance.drawer.description')}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-5 px-4 py-4">
                {/* Day Summary */}
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3.5 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-slate-900">
                      {ledger?.full_name ?? selectedEmployee?.full_name ?? ''}
                    </span>
                    <span className="text-slate-600 tabular-nums">
                      {formatRowDate(selectedDayRow.localDate, locale)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-3 text-slate-600 tabular-nums">
                    <span>
                      {t('attendance.columns.netHours')}:{' '}
                      {formatCompactHours(
                        selectedDayRow.summary?.total_net_minutes ?? 0,
                      )}
                    </span>
                    <span>
                      {t('attendance.columns.breakMinutes')}:{' '}
                      {selectedDayRow.effectiveBreakMinutes} min
                    </span>
                  </div>
                </div>

                {/* Chronological Segments */}
                {(selectedDayRow.summary?.segments ?? []).length > 0 && (
                  <div className="space-y-2">
                    <h3 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                      {t('attendance.redesign.chronologicalSegments')}
                    </h3>
                    <div className="space-y-1.5">
                      {(selectedDayRow.summary?.segments ?? []).map(
                        (seg, idx) => (
                          <div
                            key={`${selectedDayRow.localDate}-dseg-${idx}`}
                            className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-xs tabular-nums"
                          >
                            <span className="font-medium text-slate-800">
                              {seg.type === 'work'
                                ? t('attendance.redesign.legendWork')
                                : `${t('attendance.redesign.legendBreak')} (${seg.is_paid ? t('attendance.redesign.paid') : t('attendance.redesign.unpaid')})`}
                            </span>
                            <span className="text-slate-600">
                              {formatTimeHm(seg.start_at, locale, storeTimeZone)}
                              {' → '}
                              {seg.end_at
                                ? formatTimeHm(seg.end_at, locale, storeTimeZone)
                                : t('attendance.inProgress')}
                              {' · '}
                              {formatCompactHours(seg.duration_minutes)}
                            </span>
                          </div>
                        ),
                      )}
                    </div>
                  </div>
                )}

                {/* Immutable Event Audit Trail */}
                <div className="space-y-2.5">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                    {t('attendance.drawer.auditTrail')}
                  </h3>
                  {selectedDayRow.dayEvents.length === 0 ? (
                    <p className="text-xs text-slate-600">
                      {t('attendance.drawer.noEvents')}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {selectedDayRow.dayEvents.map((ev) => (
                        <div
                          key={ev.id}
                          className="rounded-md border border-slate-200 bg-white p-3 text-xs"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <Badge
                                variant="outline"
                                className={
                                  ev.event_type === 'correction'
                                    ? 'border-amber-200 bg-amber-50 text-amber-800'
                                    : 'border-slate-200 bg-slate-100 text-slate-700'
                                }
                              >
                                {ev.event_type === 'correction' &&
                                ev.corrected_event_type
                                  ? t(
                                      `attendance.correctedTypes.${ev.corrected_event_type}`,
                                    )
                                  : t(`attendance.actions.${ev.event_type}`)}
                              </Badge>
                              <span className="text-slate-600">
                                {t(`attendance.sources.${ev.source}`)}
                              </span>
                            </div>
                            <span className="font-medium text-slate-900 tabular-nums">
                              {formatUtcDateTime(
                                ev.occurred_at_utc,
                                locale,
                                ev.store_timezone,
                              )}
                            </span>
                          </div>

                          {ev.supersedes_event_id && (
                            <p className="mt-1.5 font-mono text-[11px] text-slate-600 tabular-nums">
                              {t('attendance.drawer.supersedes')}:{' '}
                              {ev.supersedes_event_id.slice(0, 8)}
                            </p>
                          )}
                          {ev.correction_reason && (
                            <p className="mt-1 text-slate-700">
                              {t('attendance.drawer.reason')}:{' '}
                              {ev.correction_reason}
                            </p>
                          )}
                          <p className="mt-1 text-[11px] text-slate-600 tabular-nums">
                            {t('attendance.drawer.retentionUntil')}:{' '}
                            {formatUtcDateTime(
                              ev.retention_until,
                              locale,
                              ev.store_timezone,
                            )}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <Separator />

                {/* Append-Only Correction Form */}
                <div className="space-y-3">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                    {t('attendance.drawer.correctionTitle')}
                  </h3>

                  {!canCorrect || selectedDayRow.isLocked ? (
                    <div className="rounded-md border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-600">
                      {selectedDayRow.isLocked
                        ? t('attendance.redesign.lockedDay')
                        : t('attendance.drawer.readOnlyNotice')}
                    </div>
                  ) : (
                    <form
                      onSubmit={(e) => void handleCorrectionSubmit(e)}
                      className="space-y-3"
                    >
                      <div className="space-y-1.5">
                        <Label htmlFor="correction-type">
                          {t('attendance.drawer.correctedEventType')}
                        </Label>
                        <Select
                          value={correctedType}
                          onValueChange={(v) =>
                            setCorrectedType(v as CorrectedEventType)
                          }
                        >
                          <SelectTrigger
                            id="correction-type"
                            aria-label={t(
                              'attendance.drawer.correctedEventType',
                            )}
                            className="w-full bg-white"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {CORRECTED_EVENT_TYPES.map((ct) => (
                              <SelectItem key={ct} value={ct}>
                                {t(`attendance.correctedTypes.${ct}`)}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1.5">
                        <Label htmlFor="correction-occurred-at">
                          {t('attendance.drawer.occurredAtLocal')} (
                          {storeTimeZone})
                        </Label>
                        <Input
                          id="correction-occurred-at"
                          type="datetime-local"
                          required
                          value={occurredAtLocal}
                          onChange={(e) => setOccurredAtLocal(e.target.value)}
                          className="tabular-nums"
                        />
                      </div>

                      <div className="space-y-1.5">
                        <Label htmlFor="correction-supersedes">
                          {t('attendance.drawer.supersedesEvent')}
                        </Label>
                        <Select
                          value={supersedesEventId}
                          onValueChange={setSupersedesEventId}
                        >
                          <SelectTrigger
                            id="correction-supersedes"
                            aria-label={t('attendance.drawer.supersedesEvent')}
                            className="w-full bg-white"
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="none">
                              {t('attendance.drawer.noSupersede')}
                            </SelectItem>
                            {selectedDayRow.dayEvents
                              .filter((ev) => ev.event_type !== 'correction')
                              .map((ev) => (
                                <SelectItem key={ev.id} value={ev.id}>
                                  {t(`attendance.actions.${ev.event_type}`)} ·{' '}
                                  {formatUtcDateTime(
                                    ev.occurred_at_utc,
                                    locale,
                                    ev.store_timezone,
                                  )}{' '}
                                  ({ev.id.slice(0, 8)})
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                      </div>

                      <div className="space-y-1.5">
                        <Label htmlFor="correction-reason">
                          {t('attendance.drawer.correctionReason')}
                        </Label>
                        <Input
                          id="correction-reason"
                          name="correction_reason"
                          autoComplete="off"
                          required
                          value={correctionReason}
                          onChange={(e) => setCorrectionReason(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && isImeComposing(e)) {
                              e.preventDefault()
                            }
                          }}
                          placeholder={t(
                            'attendance.drawer.correctionReasonPlaceholder',
                          )}
                        />
                      </div>

                      {drawerError && (
                        <div
                          role="alert"
                          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                        >
                          {drawerError}
                        </div>
                      )}

                      {drawerSuccess && (
                        <div
                          role="status"
                          aria-live="polite"
                          className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
                        >
                          {drawerSuccess}
                        </div>
                      )}

                      <SheetFooter className="px-0 pt-2">
                        <Button
                          type="submit"
                          disabled={submittingCorrection}
                          className="w-full bg-[#2563EB] text-white hover:bg-blue-700 sm:w-auto"
                        >
                          {submittingCorrection
                            ? t('attendance.drawer.submitting')
                            : t('attendance.drawer.submit')}
                        </Button>
                      </SheetFooter>
                    </form>
                  )}
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <ManualDayClockSheet
        open={manualDayOpen}
        onOpenChange={setManualDayOpen}
        employees={employees}
        canSelectEmployee={canCorrect}
        defaultEmployeeId={targetEmployeeId}
        onSuccess={() => {
          setActionFeedback({
            kind: 'success',
            text: isEs
              ? 'Jornada registrada correctamente.'
              : '整日工时补录已保存并更新台账。',
          })
          setLedgerRevision((v) => v + 1)
        }}
      />
    </div>
  )
}
