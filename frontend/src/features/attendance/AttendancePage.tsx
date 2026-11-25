import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { Download } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type {
  AlertSeverity,
  AttendanceEventResponse,
  AttendanceEventType,
  CorrectedEventType,
  DailyAttendanceSummaryResponse,
  EmployeeAttendanceLedgerResponse,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/features/auth/AuthContext'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'

type PunchAction = Exclude<AttendanceEventType, 'correction'>

const PUNCH_ACTIONS: PunchAction[] = [
  'clock_in',
  'break_start',
  'break_end',
  'clock_out',
]

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

interface DailyLedgerRow {
  rowKey: string
  summary: DailyAttendanceSummaryResponse
  employeeCode: string
  fullName: string
  storeCode: string
  storeName: string
  events: AttendanceEventResponse[]
}

function formatLocalDate(
  localDate: string,
  locale: string,
  timeZone: string,
): string {
  const parsed = new Date(`${localDate}T12:00:00Z`)
  if (Number.isNaN(parsed.getTime())) {
    return localDate
  }
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeZone,
  }).format(parsed)
}

function formatTimeOnly(
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
    timeZone,
  }).format(parsed)
}

function formatDecimalHours(value: string | number, locale: string): string {
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) {
    return String(value)
  }
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(num)
}

/**
 * Convert a `YYYY-MM-DDTHH:mm` wall-clock string in `timeZone` to a UTC ISO string.
 */
function localDateTimeInZoneToUtcIso(
  localDateTime: string,
  timeZone: string,
): string {
  const [datePart, timePart] = localDateTime.split('T')
  if (!datePart || !timePart) {
    return new Date().toISOString()
  }
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
      if (part.type !== 'literal') {
        map[part.type] = Number(part.value)
      }
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

export function AttendancePage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const role = activeTenant?.role
  const canCorrect =
    role === 'owner' || role === 'hr' || role === 'store_manager'
  const canSelfClock = role !== 'accountant'

  const [ledgerItems, setLedgerItems] = useState<
    EmployeeAttendanceLedgerResponse[]
  >([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [employeeFilter, setEmployeeFilter] = useState<string>('all')
  const [dateFrom, setDateFrom] = useState<string>('')
  const [dateTo, setDateTo] = useState<string>('')
  const [exportingCsv, setExportingCsv] = useState(false)

  const [selfClockAction, setSelfClockAction] = useState<PunchAction | null>(
    null,
  )
  const [selfClockFeedback, setSelfClockFeedback] = useState<{
    kind: 'success' | 'error'
    text: string
  } | null>(null)

  const [selectedRowKey, setSelectedRowKey] = useState<string | null>(null)
  const [correctedType, setCorrectedType] =
    useState<CorrectedEventType>('clock_out')
  const [occurredAtLocal, setOccurredAtLocal] = useState<string>('')
  const [supersedesEventId, setSupersedesEventId] = useState<string>('none')
  const [correctionReason, setCorrectionReason] = useState<string>('')
  const [submittingCorrection, setSubmittingCorrection] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)
  const [drawerSuccess, setDrawerSuccess] = useState<string | null>(null)

  const loadMetadata = useCallback(async () => {
    const [storeRes, empRes] = await Promise.all([
      apiClient.GET('/api/v1/stores'),
      apiClient.GET('/api/v1/employees'),
    ])
    if (storeRes.data) {
      setStores(storeRes.data.items)
    }
    if (empRes.data) {
      setEmployees(empRes.data.items)
    }
  }, [])

  const loadLedger = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const res = await apiClient.GET('/api/v1/attendance/ledger', {
        params: {
          query: {
            store_id: storeFilter !== 'all' ? storeFilter : undefined,
            employee_id: employeeFilter !== 'all' ? employeeFilter : undefined,
            date_from: dateFrom || undefined,
            date_to: dateTo || undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        return
      }
      setLedgerItems(res.data.items)
    } finally {
      setLoading(false)
    }
  }, [storeFilter, employeeFilter, dateFrom, dateTo, t])

  useEffect(() => {
    setStoreFilter('all')
    setEmployeeFilter('all')
    setSelectedRowKey(null)
    setSelfClockFeedback(null)
    void loadMetadata()
  }, [loadMetadata, activeTenant?.tenant_id])

  useEffect(() => {
    void loadLedger()
  }, [loadLedger, activeTenant?.tenant_id])

  const kpis = useMemo(() => {
    let totalHours = 0
    let overtimeHours = 0
    let activeAlerts = 0
    let correctionsCount = 0

    for (const item of ledgerItems) {
      totalHours += Number(item.total_net_hours) || 0
      overtimeHours += Number(item.overtime_hours) || 0
      activeAlerts += item.alerts.length
      correctionsCount += item.events.filter(
        (ev) => ev.event_type === 'correction',
      ).length
    }

    return {
      totalHours,
      overtimeHours,
      activeAlerts,
      correctionsCount,
    }
  }, [ledgerItems])

  const dailyRows = useMemo<DailyLedgerRow[]>(() => {
    const rows: DailyLedgerRow[] = []
    for (const emp of ledgerItems) {
      for (const summary of emp.daily_summaries) {
        rows.push({
          rowKey: `${emp.employee_id}:${summary.local_date}`,
          summary,
          employeeCode: emp.employee_code,
          fullName: emp.full_name,
          storeCode: emp.store_code,
          storeName: emp.store_name,
          events: emp.events,
        })
      }
    }
    rows.sort((a, b) => {
      const dateCmp = b.summary.local_date.localeCompare(a.summary.local_date)
      if (dateCmp !== 0) return dateCmp
      return a.fullName.localeCompare(b.fullName)
    })
    return rows
  }, [ledgerItems])

  const selectedRow = useMemo(
    () => dailyRows.find((r) => r.rowKey === selectedRowKey) ?? null,
    [dailyRows, selectedRowKey],
  )

  const openRowDrawer = useCallback((row: DailyLedgerRow) => {
    setSelectedRowKey(row.rowKey)
    setCorrectedType('clock_out')
    setOccurredAtLocal(`${row.summary.local_date}T18:00`)
    setSupersedesEventId('none')
    setCorrectionReason('')
    setDrawerError(null)
    setDrawerSuccess(null)
  }, [])

  const handleSelfClock = async (action: PunchAction) => {
    setSelfClockAction(action)
    setSelfClockFeedback(null)
    try {
      const res = await apiClient.POST('/api/v1/attendance/clock', {
        body: {
          event_type: action,
          source: 'web',
        },
      })
      if (res.error || !res.data) {
        setSelfClockFeedback({
          kind: 'error',
          text: extractApiError(res.error, t('common.error')),
        })
        return
      }
      const formattedTime = formatUtcDateTime(
        res.data.event.occurred_at_utc,
        locale,
        res.data.event.store_timezone,
      )
      setSelfClockFeedback({
        kind: 'success',
        text: t('attendance.selfClock.success', {
          action: t(`attendance.actions.${action}`),
          time: formattedTime,
        }),
      })
      await loadLedger()
    } finally {
      setSelfClockAction(null)
    }
  }

  const handleExportCsv = async () => {
    setExportingCsv(true)
    setErrorBanner(null)
    try {
      const params = new URLSearchParams()
      if (storeFilter !== 'all') params.set('store_id', storeFilter)
      if (employeeFilter !== 'all') params.set('employee_id', employeeFilter)
      if (dateFrom) params.set('date_from', dateFrom)
      if (dateTo) params.set('date_to', dateTo)

      const qs = params.toString()
      const url = `${getApiBaseUrl()}/api/v1/attendance/export.csv${qs ? `?${qs}` : ''}`
      const headers = await getAuthHeaders()
      const response = await fetch(url, { headers })
      if (!response.ok) {
        setErrorBanner(t('common.error'))
        return
      }
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = 'registro-jornada-itss.csv'
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(objectUrl)
    } finally {
      setExportingCsv(false)
    }
  }

  const handleCorrectionSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!selectedRow || !canCorrect) return

    const trimmedReason = correctionReason.trim()
    if (!trimmedReason) {
      setDrawerError(t('attendance.drawer.reasonRequired'))
      return
    }

    setSubmittingCorrection(true)
    setDrawerError(null)
    setDrawerSuccess(null)
    try {
      const occurredAtUtc = localDateTimeInZoneToUtcIso(
        occurredAtLocal,
        selectedRow.summary.store_timezone,
      )
      const res = await apiClient.POST('/api/v1/attendance/corrections', {
        body: {
          employee_id: selectedRow.summary.employee_id,
          store_id: selectedRow.summary.store_id,
          corrected_event_type: correctedType,
          occurred_at_utc: occurredAtUtc,
          supersedes_event_id:
            supersedesEventId !== 'none' ? supersedesEventId : null,
          correction_reason: trimmedReason,
        },
      })
      if (res.error || !res.data) {
        setDrawerError(extractApiError(res.error, t('common.error')))
        return
      }
      setCorrectionReason('')
      setSupersedesEventId('none')
      setDrawerSuccess(t('attendance.drawer.savedNotice'))
      await loadLedger()
    } finally {
      setSubmittingCorrection(false)
    }
  }

  const columns = useMemo<ColumnDef<DailyLedgerRow>[]>(
    () => [
      {
        id: 'employee',
        header: t('attendance.columns.employee'),
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium text-slate-900">
              {row.original.fullName}
            </span>
            <span className="text-xs text-slate-500 tabular-nums">
              {row.original.employeeCode} · {row.original.storeCode}
            </span>
          </div>
        ),
      },
      {
        id: 'local_date',
        header: t('attendance.columns.date'),
        cell: ({ row }) => (
          <span className="text-slate-700 tabular-nums">
            {formatLocalDate(
              row.original.summary.local_date,
              locale,
              row.original.summary.store_timezone,
            )}
          </span>
        ),
      },
      {
        id: 'clock_in_out',
        header: t('attendance.columns.clockInOut'),
        cell: ({ row }) => {
          const tz = row.original.summary.store_timezone
          return (
            <div className="flex flex-col gap-0.5 text-xs text-slate-700 tabular-nums">
              {row.original.summary.sessions.map((session, idx) => (
                <span key={`${row.original.rowKey}-${idx}`}>
                  {formatTimeOnly(session.clock_in_utc, locale, tz)}
                  {' → '}
                  {session.clock_out_utc
                    ? formatTimeOnly(session.clock_out_utc, locale, tz)
                    : session.has_open_break
                      ? t('attendance.onBreak')
                      : t('attendance.inProgress')}
                </span>
              ))}
            </div>
          )
        },
      },
      {
        id: 'break_minutes',
        header: t('attendance.columns.breakMinutes'),
        cell: ({ row }) => (
          <span className="text-slate-700 tabular-nums">
            {new Intl.NumberFormat(locale).format(
              row.original.summary.total_break_minutes,
            )}{' '}
            min
          </span>
        ),
      },
      {
        id: 'net_hours',
        header: t('attendance.columns.netHours'),
        cell: ({ row }) => (
          <span className="font-medium text-slate-900 tabular-nums">
            {formatDecimalHours(row.original.summary.total_net_hours, locale)} h
          </span>
        ),
      },
      {
        id: 'correction',
        header: t('attendance.columns.correction'),
        cell: ({ row }) =>
          row.original.summary.has_corrections ? (
            <Badge
              variant="outline"
              className="border-amber-200 bg-amber-50 text-amber-800"
            >
              {t('attendance.corrected')}
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-slate-200 bg-slate-100 text-slate-600"
            >
              {t('attendance.original')}
            </Badge>
          ),
      },
      {
        id: 'alerts',
        header: t('attendance.columns.alerts'),
        cell: ({ row }) => {
          const alerts = row.original.summary.alerts
          if (alerts.length === 0) {
            return (
              <Badge
                variant="outline"
                className="border-emerald-200 bg-emerald-50 text-emerald-700"
              >
                {t('attendance.compliant')}
              </Badge>
            )
          }
          return (
            <div className="flex flex-wrap gap-1">
              {alerts.map((alert, idx) => (
                <Badge
                  key={`${alert.code}-${idx}`}
                  variant="outline"
                  className={ALERT_BADGE_CLASS[alert.severity]}
                >
                  {t(`attendance.alertCodes.${alert.code}`)}
                </Badge>
              ))}
            </div>
          )
        },
      },
    ],
    [t, locale],
  )

  const table = useReactTable({
    data: dailyRows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            {t('attendance.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('attendance.subtitle')}</p>
        </div>

        <Button
          type="button"
          variant="outline"
          disabled={exportingCsv}
          onClick={() => void handleExportCsv()}
        >
          <Download className="h-4 w-4" aria-hidden="true" />
          {exportingCsv ? t('attendance.exporting') : t('attendance.exportCsv')}
        </Button>
      </div>

      {!canCorrect && (
        <div className="rounded-md border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-600">
          {t('attendance.readOnlyHint')}
        </div>
      )}

      {/* 4-Card KPI Strip */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium text-slate-500">
            {t('attendance.kpi.totalHours')}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
            {formatDecimalHours(kpis.totalHours, locale)} h
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium text-slate-500">
            {t('attendance.kpi.overtimeHours')}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
            {formatDecimalHours(kpis.overtimeHours, locale)} h
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium text-slate-500">
            {t('attendance.kpi.activeAlerts')}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
            {new Intl.NumberFormat(locale).format(kpis.activeAlerts)}
          </p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-medium text-slate-500">
            {t('attendance.kpi.corrections')}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
            {new Intl.NumberFormat(locale).format(kpis.correctionsCount)}
          </p>
        </div>
      </div>

      {/* Compact Web Self-Clock Bar */}
      {canSelfClock && (
        <section className="rounded-md border border-slate-200 bg-white px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">
                {t('attendance.selfClock.title')}
              </h2>
              <p className="text-xs text-slate-500">
                {t('attendance.selfClock.subtitle')}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {PUNCH_ACTIONS.map((action) => (
                <Button
                  key={action}
                  type="button"
                  size="sm"
                  variant={
                    action === 'clock_in' || action === 'clock_out'
                      ? 'default'
                      : 'outline'
                  }
                  disabled={selfClockAction !== null}
                  onClick={() => void handleSelfClock(action)}
                >
                  {t(`attendance.actions.${action}`)}
                </Button>
              ))}
            </div>
          </div>

          {selfClockFeedback && (
            <div
              role={selfClockFeedback.kind === 'error' ? 'alert' : 'status'}
              aria-live="polite"
              className={
                selfClockFeedback.kind === 'error'
                  ? 'mt-2.5 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700'
                  : 'mt-2.5 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700 tabular-nums'
              }
            >
              {selfClockFeedback.text}
            </div>
          )}
        </section>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* High-Density Attendance Table */}
      <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-slate-200 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <Select value={storeFilter} onValueChange={setStoreFilter}>
              <SelectTrigger
                aria-label={t('attendance.filters.storeLabel')}
                className="h-8 min-w-40 bg-white text-xs"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('attendance.filters.allStores')}
                </SelectItem>
                {stores.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.code} · {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={employeeFilter} onValueChange={setEmployeeFilter}>
              <SelectTrigger
                aria-label={t('attendance.filters.employeeLabel')}
                className="h-8 min-w-44 bg-white text-xs"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('attendance.filters.allEmployees')}
                </SelectItem>
                {employees.map((emp) => (
                  <SelectItem key={emp.id} value={emp.id}>
                    {emp.employee_code} · {emp.full_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Input
              type="date"
              aria-label={t('attendance.filters.dateFrom')}
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
              className="h-8 w-auto text-xs tabular-nums"
            />
            <Input
              type="date"
              aria-label={t('attendance.filters.dateTo')}
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
              className="h-8 w-auto text-xs tabular-nums"
            />
          </div>

          <div className="flex items-center gap-3">
            <h2 id="attendance-table-heading" className="sr-only">
              {t('attendance.tableHeading')}
            </h2>
            <span className="text-xs text-slate-500 tabular-nums">
              {t('attendance.count', { count: dailyRows.length })}
            </span>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="attendance-table-heading"
          className="overflow-x-auto focus-visible:ring-2 focus-visible:ring-teal-700 focus-visible:outline-none"
        >
          <Table>
            <TableHeader className="sticky top-0 bg-slate-50">
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id} className="border-slate-200">
                  {headerGroup.headers.map((header) => (
                    <TableHead
                      key={header.id}
                      className="px-4 py-2.5 text-xs font-medium whitespace-nowrap text-slate-600"
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(
                            header.column.columnDef.header,
                            header.getContext(),
                          )}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {loading ? (
                Array.from({ length: 3 }).map((_, idx) => (
                  <TableRow key={idx}>
                    <TableCell colSpan={columns.length} className="px-4 py-3">
                      <Skeleton className="h-5 w-full" />
                    </TableCell>
                  </TableRow>
                ))
              ) : table.getRowModel().rows.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={columns.length}
                    className="px-4 py-10 text-center text-sm text-slate-500"
                  >
                    {t('attendance.empty')}
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    tabIndex={0}
                    onClick={() => openRowDrawer(row.original)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        openRowDrawer(row.original)
                      }
                    }}
                    className="cursor-pointer border-slate-200 hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id} className="px-4 py-2.5 text-sm">
                        {flexRender(
                          cell.column.columnDef.cell,
                          cell.getContext(),
                        )}
                      </TableCell>
                    ))}
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      {/* Right Sheet Drawer: Immutable Event Audit Trail & Append-Only Correction */}
      <Sheet
        open={selectedRow !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedRowKey(null)
        }}
      >
        <SheetContent className="w-full overflow-y-auto overscroll-contain bg-white sm:max-w-xl">
          {selectedRow && (
            <>
              <SheetHeader className="border-b border-slate-200 pb-4">
                <SheetTitle>{t('attendance.drawer.title')}</SheetTitle>
                <SheetDescription>
                  {t('attendance.drawer.description')}
                </SheetDescription>
              </SheetHeader>

              <div className="space-y-5 px-4 py-4">
                {/* Day Summary */}
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3.5 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold text-slate-900">
                      {selectedRow.fullName} ({selectedRow.employeeCode})
                    </span>
                    <span className="text-slate-600 tabular-nums">
                      {formatLocalDate(
                        selectedRow.summary.local_date,
                        locale,
                        selectedRow.summary.store_timezone,
                      )}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-3 text-slate-600 tabular-nums">
                    <span>
                      {selectedRow.storeCode} · {selectedRow.storeName}
                    </span>
                    <span>
                      {t('attendance.columns.netHours')}:{' '}
                      {formatDecimalHours(
                        selectedRow.summary.total_net_hours,
                        locale,
                      )}{' '}
                      h
                    </span>
                    <span>
                      {t('attendance.columns.breakMinutes')}:{' '}
                      {selectedRow.summary.total_break_minutes} min
                    </span>
                  </div>
                </div>

                {/* Immutable Event Audit Trail */}
                <div className="space-y-2.5">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-700 uppercase">
                    {t('attendance.drawer.auditTrail')}
                  </h3>
                  {selectedRow.events.length === 0 ? (
                    <p className="text-xs text-slate-500">
                      {t('attendance.drawer.noEvents')}
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {selectedRow.events.map((ev) => (
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
                              <span className="text-slate-500">
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
                            <p className="mt-1.5 font-mono text-[11px] text-slate-500 tabular-nums">
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
                          <p className="mt-1 text-[11px] text-slate-400 tabular-nums">
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

                  {!canCorrect ? (
                    <div className="rounded-md border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-xs text-slate-600">
                      {t('attendance.drawer.readOnlyNotice')}
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
                          {selectedRow.summary.store_timezone})
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
                            {selectedRow.events
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
                          className="w-full sm:w-auto"
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
    </div>
  )
}
