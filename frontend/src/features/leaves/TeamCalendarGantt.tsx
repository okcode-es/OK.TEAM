import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Calendar,
  ChevronLeft,
  ChevronRight,
  Search,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  StoreResponse,
  TeamCalendarEntryResponse,
  TeamCalendarResponse,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface TeamCalendarGanttProps {
  stores: StoreResponse[]
}

const DAY_COL_WIDTH = 34

function addMonths(year: number, monthIndex: number, delta: number) {
  const dt = new Date(Date.UTC(year, monthIndex + delta, 1))
  return { year: dt.getUTCFullYear(), monthIndex: dt.getUTCMonth() }
}

function getIsoWeekNumber(date: Date): number {
  const tmp = new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()),
  )
  const dayNum = tmp.getUTCDay() || 7
  tmp.setUTCDate(tmp.getUTCDate() + 4 - dayNum)
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1))
  return Math.ceil(((tmp.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7)
}

function enumerateDays(fromIso: string, toIso: string): string[] {
  const result: string[] = []
  const [sy, sm, sd] = fromIso.split('-').map(Number)
  const [ey, em, ed] = toIso.split('-').map(Number)
  const cur = new Date(Date.UTC(sy, sm - 1, sd))
  const end = new Date(Date.UTC(ey, em - 1, ed))
  while (cur <= end) {
    result.push(cur.toISOString().slice(0, 10))
    cur.setUTCDate(cur.getUTCDate() + 1)
  }
  return result
}

function getEntryBarStyle(entry: TeamCalendarEntryResponse): string {
  if (entry.status === 'pending_manager') {
    return 'bg-amber-400/90 text-slate-950 border border-amber-500/60'
  }
  if (entry.status === 'pending_hr') {
    return 'bg-sky-500/90 text-white border border-sky-600/60'
  }
  if (entry.display_type === 'absence_redacted') {
    return 'bg-slate-500/85 text-white'
  }
  if (
    entry.display_type === 'sick_leave' ||
    entry.display_type === 'medical_appointment'
  ) {
    return 'bg-rose-500/85 text-white'
  }
  if (entry.display_type === 'vacation') {
    return 'bg-emerald-600/90 text-white'
  }
  return 'bg-sky-600/90 text-white'
}

export function TeamCalendarGantt({ stores }: TeamCalendarGanttProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')

  const now = new Date()
  const [baseYear, setBaseYear] = useState(now.getUTCFullYear())
  const [baseMonth, setBaseMonth] = useState(now.getUTCMonth())
  const [spanMonths, setSpanMonths] = useState<1 | 2>(1)
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [departmentFilter, setDepartmentFilter] = useState('')

  const [data, setData] = useState<TeamCalendarResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const todayIso = useMemo(() => new Date().toISOString().slice(0, 10), [])

  const { fromDate, toDate } = useMemo(() => {
    const start = new Date(Date.UTC(baseYear, baseMonth, 1))
    const end = new Date(Date.UTC(baseYear, baseMonth + spanMonths, 0))
    return {
      fromDate: start.toISOString().slice(0, 10),
      toDate: end.toISOString().slice(0, 10),
    }
  }, [baseYear, baseMonth, spanMonths])

  const loadCalendar = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiClient.GET('/api/v1/leaves/team-calendar', {
        params: {
          query: {
            from_date: fromDate,
            to_date: toDate,
            store_id: storeFilter !== 'all' ? storeFilter : undefined,
            department: departmentFilter.trim() || undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setError(extractApiError(res.error, t('common.error')))
        return
      }
      setData(res.data)
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate, storeFilter, departmentFilter, t])

  useEffect(() => {
    void loadCalendar()
  }, [loadCalendar])

  const days = useMemo(() => enumerateDays(fromDate, toDate), [fromDate, toDate])

  const holidayMap = useMemo(() => {
    const map = new Map<string, string>()
    for (const h of data?.holidays ?? []) {
      map.set(h.date, isEs ? h.name_es : h.name_zh)
    }
    return map
  }, [data?.holidays, isEs])

  const monthGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; count: number }> = []
    for (const dIso of days) {
      const key = dIso.slice(0, 7)
      const last = groups[groups.length - 1]
      if (last && last.key === key) {
        last.count += 1
      } else {
        const dt = new Date(`${dIso}T12:00:00Z`)
        const label = new Intl.DateTimeFormat(locale, {
          month: 'long',
          year: 'numeric',
        }).format(dt)
        groups.push({ key, label, count: 1 })
      }
    }
    return groups
  }, [days, locale])

  const weekGroups = useMemo(() => {
    const groups: Array<{ key: string; label: string; count: number }> = []
    for (const dIso of days) {
      const dt = new Date(`${dIso}T12:00:00Z`)
      const wk = getIsoWeekNumber(dt)
      const key = `${dIso.slice(0, 4)}-W${wk}`
      const last = groups[groups.length - 1]
      if (last && last.key === key) {
        last.count += 1
      } else {
        groups.push({ key, label: `S${wk}`, count: 1 })
      }
    }
    return groups
  }, [days])

  const todayIndex = useMemo(() => days.indexOf(todayIso), [days, todayIso])

  const jumpToToday = () => {
    const tNow = new Date()
    setBaseYear(tNow.getUTCFullYear())
    setBaseMonth(tNow.getUTCMonth())
    window.setTimeout(() => {
      if (scrollRef.current && todayIndex >= 0) {
        scrollRef.current.scrollTo({
          left: Math.max(0, todayIndex * DAY_COL_WIDTH - 200),
          behavior: 'smooth',
        })
      }
    }, 100)
  }

  const getEntryLabel = (displayType: string): string => {
    return t(`leaves.types.${displayType}`, { defaultValue: displayType })
  }

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200/75 bg-white p-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={jumpToToday}
            className="h-8 text-xs"
          >
            {isEs ? 'Hoy' : '今天'}
          </Button>

          <div className="inline-flex items-center rounded-lg border border-slate-200 bg-white">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => {
                const prev = addMonths(baseYear, baseMonth, -1)
                setBaseYear(prev.year)
                setBaseMonth(prev.monthIndex)
              }}
              aria-label={isEs ? 'Mes anterior' : '上个月'}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </Button>
            <span className="px-2.5 text-xs font-semibold text-slate-800 capitalize tabular-nums">
              {fromDate.slice(0, 7)}
              {spanMonths === 2 ? ` → ${toDate.slice(0, 7)}` : ''}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={() => {
                const next = addMonths(baseYear, baseMonth, 1)
                setBaseYear(next.year)
                setBaseMonth(next.monthIndex)
              }}
              aria-label={isEs ? 'Mes siguiente' : '下个月'}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>

          <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
            <button
              type="button"
              onClick={() => setSpanMonths(1)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                spanMonths === 1
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {isEs ? '1 mes' : '单月'}
            </button>
            <button
              type="button"
              onClick={() => setSpanMonths(2)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                spanMonths === 2
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {isEs ? '2 meses' : '双月'}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={storeFilter} onValueChange={setStoreFilter}>
            <SelectTrigger className="h-8 min-w-40 bg-white text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                {t('dashboard.allStores')}
              </SelectItem>
              {stores.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.code} · {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <div className="relative w-44">
            <Search
              className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={departmentFilter}
              onChange={(e) => setDepartmentFilter(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && isImeComposing(e)) e.preventDefault()
              }}
              placeholder={t('dashboard.departmentPlaceholder')}
              className="h-8 pl-8 text-xs"
            />
          </div>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-3 px-1 text-[11px] text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-emerald-600" />
          {t('leaves.types.vacation')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" />
          {t('leaves.types.sick_leave')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-amber-400" />
          {t('leaves.statuses.pending')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-sky-600" />
          {t('leaves.types.day_off')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm bg-slate-500" />
          {isEs ? 'Ausencia (privado)' : '缺勤（隐私脱敏）'}
        </span>
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          {error}
        </div>
      )}

      {loading ? (
        <Skeleton className="h-80 w-full rounded-xl" />
      ) : !data || data.members.length === 0 ? (
        <EmptyState
          icon={Calendar}
          title={isEs ? 'Sin miembros en el calendario' : '暂无团队日历数据'}
          description={
            isEs
              ? 'Ajuste los filtros de centro o departamento para ver la disponibilidad del equipo.'
              : '请调整门店或部门筛选条件以查看团队休假与排班时间轴。'
          }
        />
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200/75 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div ref={scrollRef} className="overflow-x-auto">
            <div
              className="relative min-w-max"
              style={{
                width: `${224 + days.length * DAY_COL_WIDTH}px`,
              }}
            >
              {/* Row 1: Months */}
              <div className="flex border-b border-slate-200 bg-slate-50/90">
                <div className="sticky left-0 z-20 flex w-56 shrink-0 items-center gap-2 border-r border-slate-200 bg-slate-50 px-4 py-2 text-xs font-semibold text-slate-700">
                  <Users className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
                  <span className="tabular-nums">
                    {data.members.length} {isEs ? 'personas' : '人'}
                  </span>
                </div>
                <div className="flex">
                  {monthGroups.map((mg) => (
                    <div
                      key={mg.key}
                      style={{ width: `${mg.count * DAY_COL_WIDTH}px` }}
                      className="border-r border-slate-200 px-3 py-2 text-xs font-semibold text-slate-800 capitalize"
                    >
                      {mg.label}
                    </div>
                  ))}
                </div>
              </div>

              {/* Row 2: ISO Weeks */}
              <div className="flex border-b border-slate-200 bg-slate-50/50">
                <div className="sticky left-0 z-20 w-56 shrink-0 border-r border-slate-200 bg-slate-50 px-4 py-1 text-[10px] font-medium text-slate-400 uppercase">
                  {isEs ? 'Semana ISO' : 'ISO 周次'}
                </div>
                <div className="flex">
                  {weekGroups.map((wg) => (
                    <div
                      key={wg.key}
                      style={{ width: `${wg.count * DAY_COL_WIDTH}px` }}
                      className="border-r border-slate-200/80 px-1.5 py-1 text-center text-[10px] font-semibold text-slate-500 tabular-nums"
                    >
                      {wg.label}
                    </div>
                  ))}
                </div>
              </div>

              {/* Row 3: Days + Weekday + Holiday Badges */}
              <div className="flex border-b border-slate-200 bg-white">
                <div className="sticky left-0 z-20 w-56 shrink-0 border-r border-slate-200 bg-white px-4 py-2 text-[11px] font-semibold text-slate-500">
                  {isEs ? 'Empleado' : '员工'}
                </div>
                <div className="flex">
                  {days.map((dIso) => {
                    const dt = new Date(`${dIso}T12:00:00Z`)
                    const dow = dt.getUTCDay()
                    const isWeekend = dow === 0 || dow === 6
                    const isToday = dIso === todayIso
                    const holidayName = holidayMap.get(dIso)
                    const wkLabel = new Intl.DateTimeFormat(locale, {
                      weekday: 'narrow',
                    }).format(dt)
                    const dayNum = dIso.slice(8, 10)

                    return (
                      <div
                        key={dIso}
                        style={{ width: `${DAY_COL_WIDTH}px` }}
                        title={holidayName || dIso}
                        className={cn(
                          'relative flex flex-col items-center justify-center border-r border-slate-100 py-1.5 text-center',
                          isWeekend && 'bg-slate-100/70',
                          isToday && 'bg-blue-50',
                        )}
                      >
                        <span className="text-[10px] text-slate-400">
                          {wkLabel}
                        </span>
                        <span
                          className={cn(
                            'text-xs font-semibold tabular-nums',
                            isToday
                              ? 'flex h-5 w-5 items-center justify-center rounded-full bg-[#2563EB] text-white'
                              : holidayName
                                ? 'text-rose-600'
                                : 'text-slate-700',
                          )}
                        >
                          {dayNum}
                        </span>
                        {holidayName && (
                          <Badge
                            variant="outline"
                            className="mt-0.5 h-1.5 w-3 border-none bg-rose-500 p-0"
                            aria-label={holidayName}
                          />
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Member Rows */}
              <div className="relative">
                {/* Today Vertical Line */}
                {todayIndex >= 0 && (
                  <div
                    aria-hidden="true"
                    className="pointer-events-none absolute top-0 bottom-0 z-10 w-0.5 bg-[#2563EB]"
                    style={{
                      left: `${224 + todayIndex * DAY_COL_WIDTH + DAY_COL_WIDTH / 2}px`,
                    }}
                  />
                )}

                {data.members.map((member) => (
                  <div
                    key={member.employee_id}
                    className="flex border-b border-slate-100 last:border-b-0 hover:bg-slate-50/40"
                  >
                    {/* Sticky Left Member Cell */}
                    <div className="sticky left-0 z-20 flex w-56 shrink-0 items-center gap-2.5 border-r border-slate-200 bg-white px-4 py-2.5">
                      <UserAvatar
                        avatarUrl={member.avatar_url}
                        name={member.preferred_name || member.full_name}
                        size="sm"
                      />
                      <div className="min-w-0">
                        <div className="truncate text-xs font-semibold text-slate-900">
                          {member.full_name}
                        </div>
                        <div className="truncate text-[11px] text-slate-500">
                          {member.job_title ||
                            member.department ||
                            member.store_name}
                        </div>
                      </div>
                    </div>

                    {/* Timeline Grid + Leave Bars */}
                    <div className="relative flex">
                      {days.map((dIso) => {
                        const dt = new Date(`${dIso}T12:00:00Z`)
                        const dow = dt.getUTCDay()
                        const isWeekend = dow === 0 || dow === 6
                        const holidayName = holidayMap.get(dIso)
                        return (
                          <div
                            key={dIso}
                            style={{ width: `${DAY_COL_WIDTH}px` }}
                            className={cn(
                              'h-12 border-r border-slate-100',
                              isWeekend && 'bg-slate-100/70',
                              holidayName && 'bg-rose-50/40',
                            )}
                          />
                        )
                      })}

                      {member.entries.map((entry) => {
                        const clampedStart =
                          entry.start_date < fromDate
                            ? fromDate
                            : entry.start_date
                        const clampedEnd =
                          entry.end_date > toDate ? toDate : entry.end_date
                        const startIdx = days.indexOf(clampedStart)
                        const endIdx = days.indexOf(clampedEnd)
                        if (startIdx < 0 || endIdx < startIdx) return null
                        const leftPx = startIdx * DAY_COL_WIDTH + 2
                        const widthPx =
                          (endIdx - startIdx + 1) * DAY_COL_WIDTH - 4

                        return (
                          <div
                            key={entry.leave_id}
                            title={`${getEntryLabel(entry.display_type)} (${entry.start_date} → ${entry.end_date}, ${entry.natural_days}d)`}
                            style={{
                              left: `${leftPx}px`,
                              width: `${widthPx}px`,
                            }}
                            className={cn(
                              'absolute top-2.5 z-10 flex h-7 items-center overflow-hidden rounded-md px-2 text-[11px] font-medium shadow-xs',
                              getEntryBarStyle(entry),
                            )}
                          >
                            <span className="truncate">
                              {getEntryLabel(entry.display_type)}
                            </span>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
