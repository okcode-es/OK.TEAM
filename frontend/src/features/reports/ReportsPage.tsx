import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  BarChart3,
  Building2,
  CalendarClock,
  Clock,
  Download,
  FileSpreadsheet,
  FileText,
  Megaphone,
  ShieldAlert,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type { ExecutiveSummaryResponse, StoreResponse } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useAuth } from '@/features/auth/AuthContext'
import { isImeComposing } from '@/lib/ime'

function getFirstDayOfMonthIso(): string {
  const now = new Date()
  const year = now.getUTCFullYear()
  const month = String(now.getUTCMonth() + 1).padStart(2, '0')
  return `${year}-${month}-01`
}

function getTodayIso(): string {
  return new Date().toISOString().slice(0, 10)
}

export function ReportsPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const [fromDate, setFromDate] = useState<string>(() =>
    getFirstDayOfMonthIso(),
  )
  const [toDate, setToDate] = useState<string>(() => getTodayIso())
  const [year, setYear] = useState<number>(() => new Date().getUTCFullYear())
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [departmentFilter, setDepartmentFilter] = useState<string>('')

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [summary, setSummary] = useState<ExecutiveSummaryResponse | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null)

  const loadStores = useCallback(async () => {
    try {
      const res = await apiClient.GET('/api/v1/stores')
      if (res.data) {
        setStores(res.data.items)
      }
    } catch {
      // ignore non-critical store list failure
    }
  }, [])

  const loadSummary = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const res = await apiClient.GET('/api/v1/reports/executive-summary', {
        params: {
          query: {
            from_date: fromDate || getFirstDayOfMonthIso(),
            to_date: toDate || getTodayIso(),
            store_id: storeFilter !== 'all' ? storeFilter : undefined,
            department: departmentFilter.trim() || undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('errors.GENERIC')))
        return
      }
      setSummary(res.data)
    } catch (err) {
      setErrorBanner(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate, storeFilter, departmentFilter, t])

  useEffect(() => {
    setStoreFilter('all')
    setDepartmentFilter('')
    void loadStores()
  }, [loadStores, activeTenant?.tenant_id])

  useEffect(() => {
    void loadSummary()
  }, [loadSummary, activeTenant?.tenant_id])

  const handleDownloadReport = async (
    reportKey: 'attendance-summary' | 'leave-summary' | 'headcount-roster',
    format: 'csv' | 'pdf',
  ) => {
    const busyKey = `${reportKey}:${format}`
    setDownloadingKey(busyKey)
    setErrorBanner(null)
    try {
      const params = new URLSearchParams()
      params.set('format', format)
      if (reportKey === 'attendance-summary') {
        if (fromDate) params.set('from_date', fromDate)
        if (toDate) params.set('to_date', toDate)
      } else if (reportKey === 'leave-summary') {
        params.set('year', String(year))
      }
      if (storeFilter !== 'all') params.set('store_id', storeFilter)
      if (departmentFilter.trim()) {
        params.set('department', departmentFilter.trim())
      }

      const url = `${getApiBaseUrl()}/api/v1/reports/${reportKey}?${params.toString()}`
      const headers = await getAuthHeaders()
      const response = await fetch(url, { headers })
      if (!response.ok) {
        setErrorBanner(await extractFetchError(response, t('errors.GENERIC')))
        return
      }
      const blob = await response.blob()
      const objectUrl = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = `okteam-${reportKey}.${format}`
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(objectUrl)
    } catch (err) {
      setErrorBanner(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setDownloadingKey(null)
    }
  }

  const formatHours = (val: string | number) => {
    const num = typeof val === 'number' ? val : Number(val)
    if (!Number.isFinite(num)) return String(val)
    return new Intl.NumberFormat(locale, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num)
  }

  const currentYear = new Date().getUTCFullYear()
  const yearOptions = useMemo(
    () => [currentYear - 1, currentYear, currentYear + 1],
    [currentYear],
  )

  const reportCards = [
    {
      key: 'attendance-summary' as const,
      icon: Clock,
      title: t('reports.cards.attendance.title'),
      description: t('reports.cards.attendance.description'),
    },
    {
      key: 'leave-summary' as const,
      icon: CalendarClock,
      title: t('reports.cards.leaves.title'),
      description: t('reports.cards.leaves.description'),
    },
    {
      key: 'headcount-roster' as const,
      icon: Users,
      title: t('reports.cards.roster.title'),
      description: t('reports.cards.roster.description'),
    },
  ]

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="space-y-1">
        <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-slate-900">
          <BarChart3 className="h-5 w-5 text-[#2563EB]" aria-hidden="true" />
          {t('reports.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('reports.subtitle')}</p>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 gap-3 rounded-xl border border-slate-200/75 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] sm:grid-cols-2 lg:grid-cols-5">
        <div className="space-y-1">
          <Label htmlFor="rep-from" className="text-xs text-slate-500">
            {t('reports.filters.fromDate')}
          </Label>
          <Input
            id="rep-from"
            type="date"
            value={fromDate}
            onChange={(e) => setFromDate(e.target.value)}
            className="h-9 text-xs tabular-nums"
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="rep-to" className="text-xs text-slate-500">
            {t('reports.filters.toDate')}
          </Label>
          <Input
            id="rep-to"
            type="date"
            value={toDate}
            onChange={(e) => setToDate(e.target.value)}
            className="h-9 text-xs tabular-nums"
          />
        </div>

        <div className="space-y-1">
          <Label htmlFor="rep-year" className="text-xs text-slate-500">
            {t('reports.filters.leaveYear')}
          </Label>
          <Select
            value={String(year)}
            onValueChange={(v) => setYear(Number(v))}
          >
            <SelectTrigger id="rep-year" className="h-9 w-full bg-white text-xs tabular-nums">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {yearOptions.map((y) => (
                <SelectItem key={y} value={String(y)} className="tabular-nums">
                  {y}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label htmlFor="rep-store" className="text-xs text-slate-500">
            {t('reports.filters.store')}
          </Label>
          <Select value={storeFilter} onValueChange={setStoreFilter}>
            <SelectTrigger id="rep-store" className="h-9 w-full bg-white text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('dashboard.allStores')}</SelectItem>
              {stores.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.code} · {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <Label htmlFor="rep-dept" className="text-xs text-slate-500">
            {t('reports.filters.department')}
          </Label>
          <Input
            id="rep-dept"
            type="search"
            value={departmentFilter}
            onChange={(e) => setDepartmentFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && isImeComposing(e)) {
                e.preventDefault()
              }
            }}
            placeholder={t('dashboard.departmentPlaceholder')}
            className="h-9 text-xs"
          />
        </div>
      </div>

      {errorBanner && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* Executive KPI Summary Grid */}
      {loading && !summary ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
        </div>
      ) : (
        summary && (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <Users
                      className="h-3.5 w-3.5 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.headcount')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {summary.total_active_employees}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-slate-500 tabular-nums">
                    <span>
                      {t('employees.scheduleTypes.full_time')}:{' '}
                      {summary.full_time_count}
                    </span>
                    <span>·</span>
                    <span>
                      {t('employees.scheduleTypes.part_time')}:{' '}
                      {summary.part_time_count}
                    </span>
                  </div>
                </CardContent>
              </Card>

              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <Clock
                      className="h-3.5 w-3.5 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.workedHours')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {formatHours(summary.total_worked_hours)} h
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
                    {t('reports.kpi.overtimeHours')}:{' '}
                    <span className="font-medium text-slate-700">
                      {formatHours(summary.total_overtime_hours)} h
                    </span>
                  </div>
                </CardContent>
              </Card>

              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <ShieldAlert
                      className="h-3.5 w-3.5 text-amber-600"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.alerts')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {summary.total_compliance_alerts}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    {t('reports.kpi.alertsHint')}
                  </div>
                </CardContent>
              </Card>

              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <CalendarClock
                      className="h-3.5 w-3.5 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.pendingLeaves')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {summary.pending_manager_leaves + summary.pending_hr_leaves}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-slate-500 tabular-nums">
                    <span>
                      {t('reports.kpi.pendingManager')}:{' '}
                      {summary.pending_manager_leaves}
                    </span>
                    <span>·</span>
                    <span>
                      {t('reports.kpi.pendingHr')}: {summary.pending_hr_leaves}
                    </span>
                  </div>
                </CardContent>
              </Card>

              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <CalendarClock
                      className="h-3.5 w-3.5 text-emerald-600"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.approvedVacations')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {summary.approved_vacation_days} d
                  </div>
                  {summary.approved_sick_days !== null && (
                    <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
                      {t('reports.kpi.approvedSickDays')}:{' '}
                      {summary.approved_sick_days} d
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="gap-2 py-4">
                <CardHeader className="px-5 pb-0">
                  <CardTitle className="flex items-center gap-2 text-xs font-medium text-slate-500">
                    <Megaphone
                      className="h-3.5 w-3.5 text-[#2563EB]"
                      aria-hidden="true"
                    />
                    {t('reports.kpi.pendingAck')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5">
                  <div className="text-2xl font-bold text-slate-900 tabular-nums">
                    {summary.announcements_pending_ack_count}
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">
                    {t('reports.kpi.pendingAckHint')}
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Downloadable Management Report Cards */}
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              {reportCards.map((card) => {
                const Icon = card.icon
                return (
                  <Card key={card.key} className="justify-between gap-4 py-5">
                    <CardHeader className="px-5 pb-0">
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Icon
                          className="h-4 w-4 text-[#2563EB]"
                          aria-hidden="true"
                        />
                        {card.title}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4 px-5">
                      <p className="text-xs text-slate-600">
                        {card.description}
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={downloadingKey === `${card.key}:csv`}
                          onClick={() =>
                            void handleDownloadReport(card.key, 'csv')
                          }
                          className="flex-1 text-xs"
                        >
                          <FileSpreadsheet
                            className="h-3.5 w-3.5"
                            aria-hidden="true"
                          />
                          {t('reports.exportCsv')}
                        </Button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={downloadingKey === `${card.key}:pdf`}
                          onClick={() =>
                            void handleDownloadReport(card.key, 'pdf')
                          }
                          className="flex-1 bg-[#2563EB] text-xs text-white hover:bg-blue-700"
                        >
                          <Download className="h-3.5 w-3.5" aria-hidden="true" />
                          {t('reports.exportPdf')}
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </div>

            {/* Per-Store Performance Breakdown Table */}
            <Card className="gap-3 py-5">
              <CardHeader className="px-5 pb-0">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                  <Building2
                    className="h-4 w-4 text-[#2563EB]"
                    aria-hidden="true"
                  />
                  {t('reports.storeBreakdown.title')}
                </CardTitle>
              </CardHeader>
              <CardContent className="px-5">
                {summary.by_store.length === 0 ? (
                  <p className="py-6 text-center text-xs text-slate-500">
                    {t('reports.storeBreakdown.empty')}
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border border-slate-200">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>
                            {t('reports.storeBreakdown.store')}
                          </TableHead>
                          <TableHead className="text-right">
                            {t('reports.storeBreakdown.employees')}
                          </TableHead>
                          <TableHead className="text-right">
                            {t('reports.storeBreakdown.workedHours')}
                          </TableHead>
                          <TableHead className="text-right">
                            {t('reports.storeBreakdown.overtimeHours')}
                          </TableHead>
                          <TableHead className="text-right">
                            {t('reports.storeBreakdown.alerts')}
                          </TableHead>
                          <TableHead className="text-right">
                            {t('reports.storeBreakdown.pendingLeaves')}
                          </TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {summary.by_store.map((row) => (
                          <TableRow key={row.store_id}>
                            <TableCell className="font-medium text-slate-900">
                              <span className="inline-flex items-center gap-1.5">
                                <FileText
                                  className="h-3.5 w-3.5 text-slate-400"
                                  aria-hidden="true"
                                />
                                {row.store_name}
                              </span>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {row.active_employees}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatHours(row.worked_hours)} h
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatHours(row.overtime_hours)} h
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              <Badge
                                variant="outline"
                                className={
                                  row.compliance_alerts > 0
                                    ? 'border-amber-200 bg-amber-50 text-amber-800'
                                    : 'border-slate-200 bg-slate-50 text-slate-600'
                                }
                              >
                                {row.compliance_alerts}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {row.pending_leaves}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </>
        )
      )}
    </div>
  )
}
