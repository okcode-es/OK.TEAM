import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Clock,
  Coffee,
  Lock,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  DailyAttendanceSummaryResponse,
  EmployeeResponse,
  ShiftResponse,
} from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import { useAuth } from '@/features/auth/AuthContext'
import { formatDurationHhMm } from '@/lib/spain'
import { cn } from '@/lib/utils'

interface ProfileAttendanceTabProps {
  employee: EmployeeResponse
}

function getMondayIso(date: Date): string {
  const d = new Date(date)
  const day = d.getDay()
  const diff = day === 0 ? -6 : 1 - day
  d.setDate(d.getDate() + diff)
  return d.toISOString().slice(0, 10)
}

function addDaysIso(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return dt.toISOString().slice(0, 10)
}

function formatTimeHm(
  isoUtc: string,
  locale: string,
  timeZone = 'Europe/Madrid',
): string {
  const dt = new Date(isoUtc)
  if (Number.isNaN(dt.getTime())) return '--:--'
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone,
  }).format(dt)
}

export function ProfileAttendanceTab({ employee }: ProfileAttendanceTabProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const role = activeTenant?.role
  const canEditBreakPolicy =
    role === 'owner' || role === 'hr' || role === 'store_manager'

  const [breakPolicyOpen, setBreakPolicyOpen] = useState(false)

  const [weekStart, setWeekStart] = useState<string>(() =>
    getMondayIso(new Date()),
  )
  const weekEnd = useMemo(() => addDaysIso(weekStart, 6), [weekStart])
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), [])

  const [summaries, setSummaries] = useState<DailyAttendanceSummaryResponse[]>(
    [],
  )
  const [shifts, setShifts] = useState<ShiftResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadWeek = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [ledgerRes, shiftRes] = await Promise.all([
        apiClient.GET('/api/v1/attendance/ledger', {
          params: {
            query: {
              employee_id: employee.id,
              date_from: weekStart,
              date_to: weekEnd,
            },
          },
        }),
        apiClient.GET('/api/v1/shifts/weekly', {
          params: {
            query: {
              week_start: weekStart,
              store_id: employee.primary_store_id,
            },
          },
        }),
      ])
      if (ledgerRes.error) {
        setError(extractApiError(ledgerRes.error, t('common.error')))
        return
      }
      const empRow = ledgerRes.data?.items.find(
        (e) => e.employee_id === employee.id,
      )
      setSummaries(empRow?.daily_summaries ?? [])

      const shiftRow = shiftRes.data?.rows.find(
        (r) => r.employee_id === employee.id,
      )
      setShifts(shiftRow?.shifts ?? [])
    } finally {
      setLoading(false)
    }
  }, [employee.id, employee.primary_store_id, weekStart, weekEnd, t])

  useEffect(() => {
    void loadWeek()
  }, [loadWeek])

  const weekDays = useMemo(
    () => Array.from({ length: 7 }, (_, i) => addDaysIso(weekStart, i)),
    [weekStart],
  )

  const summaryByDate = useMemo(() => {
    const map = new Map<string, DailyAttendanceSummaryResponse>()
    for (const s of summaries) {
      map.set(s.local_date, s)
    }
    return map
  }, [summaries])

  const shiftByDate = useMemo(() => {
    const map = new Map<string, ShiftResponse[]>()
    for (const sh of shifts) {
      const dKey = sh.start_at_utc.slice(0, 10)
      const list = map.get(dKey) ?? []
      list.push(sh)
      map.set(dKey, list)
    }
    return map
  }, [shifts])

  const contractedWeeklyMinutes = Math.round(
    Number(employee.contracted_weekly_hours || 40) * 60,
  )
  const defaultDailyExpectedMinutes = Math.round(contractedWeeklyMinutes / 5)

  const totalWorkedMinutes = useMemo(
    () => summaries.reduce((acc, s) => acc + s.total_net_minutes, 0),
    [summaries],
  )
  const weeklyPercent =
    contractedWeeklyMinutes > 0
      ? Math.min(
          100,
          Math.round((totalWorkedMinutes / contractedWeeklyMinutes) * 100),
        )
      : 0

  return (
    <div className="space-y-5">
      {/* Collapsible Employee Break Policy Card */}
      <Card className="gap-0 py-0">
        <button
          type="button"
          onClick={() => setBreakPolicyOpen((prev) => !prev)}
          className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left transition-colors hover:bg-slate-50/70"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-900">
            <Coffee className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
            <span>
              {isEs ? 'Regla de descanso personalizada' : '员工休息规则配置'}
            </span>
          </div>
          {breakPolicyOpen ? (
            <ChevronUp className="h-4 w-4 text-slate-500" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-4 w-4 text-slate-500" aria-hidden="true" />
          )}
        </button>
        {breakPolicyOpen && (
          <CardContent className="border-t border-slate-100 px-5 py-4">
            <BreakPolicyForm
              scopeType="employee"
              employeeId={employee.id}
              readOnly={!canEditBreakPolicy}
            />
          </CardContent>
        )}
      </Card>

      {/* Top Week Switcher */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/75 bg-white px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setWeekStart(getMondayIso(new Date()))}
            className="h-8 text-xs"
          >
            {isEs ? 'Hoy' : '今天'}
          </Button>
          <div className="inline-flex items-center rounded-lg border border-slate-200 bg-white">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => setWeekStart((prev) => addDaysIso(prev, -7))}
              aria-label={isEs ? 'Semana anterior' : '上一周'}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <span className="px-3 text-xs font-semibold text-slate-800 tabular-nums">
              {weekStart} → {weekEnd}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => setWeekStart((prev) => addDaysIso(prev, 7))}
              aria-label={isEs ? 'Semana siguiente' : '下一周'}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        <Badge
          variant="outline"
          className="border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
        >
          {isEs ? 'Vista semanal' : '周视图'}
        </Badge>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          {error}
        </div>
      )}

      {/* Side-by-side Summary Cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Left: Weekly Worked vs Target Hours */}
        <Card className="gap-3 py-5">
          <CardHeader className="px-5 pb-0">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <Clock className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
              {isEs ? 'Horas semanales registradas' : '本周已录工时 / 应录工时'}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 px-5">
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-bold tracking-tight text-slate-900 tabular-nums">
                {formatDurationHhMm(totalWorkedMinutes)}
              </span>
              <span className="text-xs font-medium text-slate-500 tabular-nums">
                {isEs ? 'Objetivo' : '目标'}:{' '}
                {formatDurationHhMm(contractedWeeklyMinutes)} ({weeklyPercent}%)
              </span>
            </div>
            <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-[#2563EB] transition-[width] duration-300"
                style={{ width: `${weeklyPercent}%` }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Right: Standard Weekly Schedule Mon-Sun */}
        <Card className="gap-3 py-5">
          <CardHeader className="px-5 pb-0">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
              <CalendarDays
                className="h-4 w-4 text-[#2563EB]"
                aria-hidden="true"
              />
              {isEs ? 'Horario semanal estándar' : '周一至周日标准排班'}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5">
            <div className="grid grid-cols-7 gap-1.5 text-center">
              {weekDays.map((dayIso, idx) => {
                const dayShifts = shiftByDate.get(dayIso) ?? []
                const plannedMin =
                  dayShifts.length > 0
                    ? dayShifts.reduce(
                        (a, s) => a + Math.round(Number(s.net_hours || 0) * 60),
                        0,
                      )
                    : idx < 5
                      ? defaultDailyExpectedMinutes
                      : 0
                const dayName = new Intl.DateTimeFormat(locale, {
                  weekday: 'short',
                }).format(new Date(`${dayIso}T12:00:00Z`))

                return (
                  <div
                    key={dayIso}
                    className={cn(
                      'rounded-lg border px-1.5 py-2',
                      dayIso === todayIso
                        ? 'border-blue-300 bg-blue-50/70'
                        : 'border-slate-200/80 bg-slate-50/60',
                    )}
                  >
                    <div className="text-[10px] font-medium text-slate-500 uppercase">
                      {dayName}
                    </div>
                    <div className="mt-1 text-xs font-semibold text-slate-900 tabular-nums">
                      {plannedMin > 0
                        ? `${(plannedMin / 60).toFixed(1).replace(/\.0$/, '')}h`
                        : '—'}
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Daily Breakdown Table (Mon-Sun) */}
      {loading ? (
        <Skeleton className="h-64 w-full rounded-xl" />
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200/75 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-slate-50/80">
                <TableRow className="border-slate-200">
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Fecha' : '日期'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Entrada / Salida' : '起止时间'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Registrado / Plan' : '已录 / 应录工时'}
                  </TableHead>
                  <TableHead className="min-w-36 px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Progreso' : '进度'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Descanso' : '休息时长'}
                  </TableHead>
                  <TableHead className="px-4 text-xs font-semibold text-slate-600">
                    {isEs ? 'Estado' : '审批状态'}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {weekDays.map((dayIso, idx) => {
                  const summary = summaryByDate.get(dayIso)
                  const dayShifts = shiftByDate.get(dayIso) ?? []
                  const isFuture = dayIso > todayIso
                  const expectedMin =
                    dayShifts.length > 0
                      ? dayShifts.reduce(
                          (a, s) =>
                            a + Math.round(Number(s.net_hours || 0) * 60),
                          0,
                        )
                      : idx < 5
                        ? defaultDailyExpectedMinutes
                        : 0
                  const workedMin = summary ? summary.total_net_minutes : 0
                  const pct =
                    expectedMin > 0
                      ? Math.min(100, Math.round((workedMin / expectedMin) * 100))
                      : workedMin > 0
                        ? 100
                        : 0

                  const firstSession = summary?.sessions[0]
                  const lastSession =
                    summary && summary.sessions.length > 0
                      ? summary.sessions[summary.sessions.length - 1]
                      : undefined
                  const hasOpenSession = Boolean(
                    summary?.sessions.some((s) => s.is_open),
                  )

                  const dayLabel = new Intl.DateTimeFormat(locale, {
                    weekday: 'short',
                    day: '2-digit',
                    month: 'short',
                  }).format(new Date(`${dayIso}T12:00:00Z`))

                  return (
                    <TableRow
                      key={dayIso}
                      className={cn(
                        'border-slate-200',
                        dayIso === todayIso && 'bg-blue-50/30',
                      )}
                    >
                      <TableCell className="px-4 py-3 text-xs font-medium text-slate-900 tabular-nums">
                        <div className="flex items-center gap-1.5">
                          <span>{dayLabel}</span>
                          {isFuture && (
                            <Lock
                              className="h-3 w-3 text-slate-400"
                              aria-label={isEs ? 'Fecha futura' : '未来日期'}
                            />
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-xs text-slate-600 tabular-nums">
                        {firstSession
                          ? `${formatTimeHm(firstSession.clock_in_utc, locale)} → ${
                              lastSession?.clock_out_utc
                                ? formatTimeHm(
                                    lastSession.clock_out_utc,
                                    locale,
                                  )
                                : isEs
                                  ? 'En curso'
                                  : '进行中'
                            }`
                          : '—'}
                      </TableCell>
                      <TableCell className="px-4 py-3 text-xs text-slate-800 tabular-nums">
                        {formatDurationHhMm(workedMin)} /{' '}
                        {formatDurationHhMm(expectedMin)}
                      </TableCell>
                      <TableCell className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className={cn(
                                'h-full rounded-full transition-[width]',
                                pct >= 100 ? 'bg-emerald-600' : 'bg-[#2563EB]',
                              )}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className="w-8 text-right text-[11px] text-slate-500 tabular-nums">
                            {pct}%
                          </span>
                        </div>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-xs text-slate-600 tabular-nums">
                        {summary
                          ? formatDurationHhMm(summary.total_break_minutes)
                          : '—'}
                      </TableCell>
                      <TableCell className="px-4 py-3">
                        {workedMin > 0 && !hasOpenSession ? (
                          <Badge
                            variant="outline"
                            className="border-emerald-200 bg-emerald-50 text-[11px] text-emerald-700"
                          >
                            {isEs ? 'Aprobado' : '已核验'}
                          </Badge>
                        ) : hasOpenSession ? (
                          <Badge
                            variant="outline"
                            className="border-amber-200 bg-amber-50 text-[11px] text-amber-800"
                          >
                            {isEs ? 'Pendiente' : '进行中'}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="border-slate-200 bg-slate-100 text-[11px] text-slate-600"
                          >
                            {isEs ? 'Sin fichar' : '未打卡'}
                          </Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  )
}
