import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Download,
  FileCheck2,
  FileSpreadsheet,
  Lock,
  RotateCcw,
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
import type {
  ContractType,
  MonthlyClosingDetailResponse,
  MonthlyClosingItemResponse,
  MonthlyClosingListItemResponse,
  MonthlyEmployeePreviewResponse,
  MonthlyReadinessPreviewResponse,
  StoreResponse,
  WorkScheduleType,
} from '@/api/types'
import type { NavSection } from '@/components/layout/AppShell'
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

interface PayrollRowData {
  employee_id: string
  employee_code: string
  employee_full_name: string
  nie_dni_masked: string | null
  contract_type: ContractType
  work_schedule_type: WorkScheduleType
  contracted_weekly_hours: string
  worked_days: number
  total_net_hours: string
  regular_hours: string
  overtime_hours: string
  annual_overtime_hours: string
  total_break_minutes: number
  vacation_days: number
  sick_leave_days: number
  paid_leave_days: number
  unpaid_leave_days: number
  warning_alerts_count: number
  violation_alerts_count: number
  attendance_report_document_id: string | null
  is_signed: boolean
  signed_at_utc: string | null
  document_sha256_hex: string | null
}

function getDefaultPeriodMonth(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
  }).formatToParts(new Date())
  const year = parts.find((p) => p.type === 'year')?.value ?? '2026'
  const month = parts.find((p) => p.type === 'month')?.value ?? '11'
  return `${year}-${month}`
}

function formatHours(value: string | number, locale: string): string {
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) return '0.00'
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(num)
}

function formatInt(value: number, locale: string): string {
  return new Intl.NumberFormat(locale).format(value)
}

function parseContentDispositionFilename(
  header: string | null,
  fallback: string,
): string {
  if (!header) return fallback
  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].trim())
    } catch {
      // fall back to ASCII filename
    }
  }
  const asciiMatch = /filename="?([^";]+)"?/i.exec(header)
  if (asciiMatch?.[1]) {
    return asciiMatch[1].trim()
  }
  return fallback
}

function previewToRow(
  emp: MonthlyEmployeePreviewResponse,
  snapshotItem?: MonthlyClosingItemResponse,
): PayrollRowData {
  return {
    ...emp,
    attendance_report_document_id:
      snapshotItem?.attendance_report_document_id ?? null,
    is_signed: snapshotItem?.is_signed ?? false,
    signed_at_utc: snapshotItem?.signed_at_utc ?? null,
    document_sha256_hex: snapshotItem?.document_sha256_hex ?? null,
  }
}

interface PayrollPageProps {
  onNavigate?: (section: NavSection) => void
}

export function PayrollPage({ onNavigate }: PayrollPageProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const role = activeTenant?.role
  const canCloseOrReopen = role === 'owner' || role === 'hr'
  const canExportCsv =
    role === 'owner' || role === 'hr' || role === 'accountant'

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [recentClosings, setRecentClosings] = useState<
    MonthlyClosingListItemResponse[]
  >([])
  const [selectedStoreId, setSelectedStoreId] = useState<string>('')
  const [periodMonth, setPeriodMonth] = useState<string>(getDefaultPeriodMonth)

  const [preview, setPreview] =
    useState<MonthlyReadinessPreviewResponse | null>(null)
  const [closingDetail, setClosingDetail] =
    useState<MonthlyClosingDetailResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [closingSubmitting, setClosingSubmitting] = useState(false)
  const [exportingCsv, setExportingCsv] = useState(false)

  // Reopen Dialog state
  const [reopenOpen, setReopenOpen] = useState(false)
  const [reopenReason, setReopenReason] = useState('')
  const [reopenError, setReopenError] = useState<string | null>(null)
  const [reopening, setReopening] = useState(false)

  // Detail Sheet state
  const [selectedRow, setSelectedRow] = useState<PayrollRowData | null>(null)
  const [generatingUrl, setGeneratingUrl] = useState(false)
  const [signedUrlCountdown, setSignedUrlCountdown] = useState<number | null>(
    null,
  )
  const [sheetError, setSheetError] = useState<string | null>(null)

  const storeMap = useMemo(() => {
    const map = new Map<string, StoreResponse>()
    for (const s of stores) map.set(s.id, s)
    return map
  }, [stores])

  const selectedStore = selectedStoreId
    ? storeMap.get(selectedStoreId)
    : undefined
  const activeTimeZone = selectedStore?.timezone ?? 'Europe/Madrid'

  const loadStoresAndHistory = useCallback(async () => {
    const [storesRes, closingsRes] = await Promise.all([
      apiClient.GET('/api/v1/stores'),
      apiClient.GET('/api/v1/payroll/closings'),
    ])
    if (storesRes.data) {
      const activeStores = storesRes.data.items.filter((s) => s.is_active)
      const list = activeStores.length > 0 ? activeStores : storesRes.data.items
      setStores(list)
      setSelectedStoreId((prev) =>
        prev && list.some((s) => s.id === prev) ? prev : (list[0]?.id ?? ''),
      )
    }
    if (closingsRes.data) {
      setRecentClosings(closingsRes.data.items)
    }
  }, [])

  const loadPeriodData = useCallback(
    async (storeId: string, month: string) => {
      if (!storeId || !/^\d{4}-\d{2}$/.test(month)) {
        setPreview(null)
        setClosingDetail(null)
        setLoading(false)
        return
      }
      setLoading(true)
      setErrorBanner(null)
      try {
        const previewRes = await apiClient.GET('/api/v1/payroll/preview', {
          params: {
            query: {
              store_id: storeId,
              period_month: month,
            },
          },
        })
        if (previewRes.error || !previewRes.data) {
          setErrorBanner(extractApiError(previewRes.error, t('common.error')))
          setPreview(null)
          setClosingDetail(null)
          return
        }
        setPreview(previewRes.data)

        const existingId = previewRes.data.existing_closing?.id
        if (existingId) {
          const detailRes = await apiClient.GET(
            '/api/v1/payroll/closings/{closing_id}',
            {
              params: { path: { closing_id: existingId } },
            },
          )
          if (detailRes.data) {
            setClosingDetail(detailRes.data)
          } else {
            setClosingDetail(null)
          }
        } else {
          setClosingDetail(null)
        }
      } finally {
        setLoading(false)
      }
    },
    [t],
  )

  useEffect(() => {
    setSelectedRow(null)
    setPreview(null)
    setClosingDetail(null)
    void loadStoresAndHistory()
  }, [loadStoresAndHistory, activeTenant?.tenant_id])

  useEffect(() => {
    if (!selectedStoreId) {
      setLoading(false)
      return
    }
    void loadPeriodData(selectedStoreId, periodMonth)
  }, [selectedStoreId, periodMonth, loadPeriodData])

  useEffect(() => {
    if (signedUrlCountdown === null || signedUrlCountdown <= 0) return
    const timer = window.setInterval(() => {
      setSignedUrlCountdown((prev) =>
        prev !== null && prev > 1 ? prev - 1 : 0,
      )
    }, 1000)
    return () => window.clearInterval(timer)
  }, [signedUrlCountdown])

  const existingClosing =
    closingDetail?.closing ?? preview?.existing_closing ?? null
  const isClosed = existingClosing?.status === 'closed'
  const isReopened = existingClosing?.status === 'reopened'

  const rows = useMemo<PayrollRowData[]>(() => {
    if (isClosed && closingDetail) {
      return closingDetail.items
    }
    const snapshotByEmp = new Map<string, MonthlyClosingItemResponse>()
    if (closingDetail) {
      for (const item of closingDetail.items) {
        snapshotByEmp.set(item.employee_id, item)
      }
    }
    return (preview?.employees ?? []).map((emp) =>
      previewToRow(emp, snapshotByEmp.get(emp.employee_id)),
    )
  }, [closingDetail, isClosed, preview?.employees])

  const kpis = useMemo(() => {
    let totalNet = 0
    let totalOvertime = 0
    let totalWarnings = 0
    let totalViolations = 0
    let signedCount = 0
    let totalReports = 0

    for (const row of rows) {
      totalNet += Number(row.total_net_hours) || 0
      totalOvertime += Number(row.overtime_hours) || 0
      totalWarnings += row.warning_alerts_count
      totalViolations += row.violation_alerts_count
      if (row.attendance_report_document_id) {
        totalReports += 1
        if (row.is_signed) signedCount += 1
      }
    }

    const signaturePercent =
      totalReports > 0 ? Math.round((signedCount / totalReports) * 100) : 0

    return {
      employeeCount: rows.length,
      totalNet,
      totalOvertime,
      totalWarnings,
      totalViolations,
      signedCount,
      totalReports,
      signaturePercent,
    }
  }, [rows])

  const actionableBlockers = useMemo(
    () =>
      (preview?.blockers ?? []).filter((b) => b.code !== 'ALREADY_CLOSED'),
    [preview?.blockers],
  )

  const handleCloseMonth = async () => {
    if (!canCloseOrReopen || !selectedStoreId || !preview?.can_close) return
    setClosingSubmitting(true)
    setErrorBanner(null)
    try {
      const res = await apiClient.POST('/api/v1/payroll/closings', {
        body: {
          store_id: selectedStoreId,
          period_month: periodMonth,
          notes: '',
        },
      })
      if (res.error || !res.data) {
        setErrorBanner(extractApiError(res.error, t('common.error')))
        return
      }
      await Promise.all([
        loadPeriodData(selectedStoreId, periodMonth),
        loadStoresAndHistory(),
      ])
    } finally {
      setClosingSubmitting(false)
    }
  }

  const handleExportCsv = async () => {
    if (!canExportCsv || !existingClosing || !isClosed) return
    setExportingCsv(true)
    setErrorBanner(null)
    try {
      const headers = await getAuthHeaders()
      const response = await fetch(
        `${getApiBaseUrl()}/api/v1/payroll/closings/${encodeURIComponent(existingClosing.id)}/gestoria-export`,
        {
          method: 'GET',
          headers,
        },
      )
      if (!response.ok) {
        setErrorBanner(await extractFetchError(response, t('errors.GENERIC')))
        return
      }

      const blob = await response.blob()
      const fallbackName = `gestoria_${selectedStore?.code ?? 'store'}_${periodMonth}_v${existingClosing.revision_number}.csv`
      const filename = parseContentDispositionFilename(
        response.headers.get('content-disposition'),
        fallbackName,
      )
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setErrorBanner(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setExportingCsv(false)
    }
  }

  const handleReopenSubmit = async (e?: FormEvent) => {
    if (e) e.preventDefault()
    if (!canCloseOrReopen || !existingClosing || !isClosed) return

    const trimmed = reopenReason.trim()
    if (!trimmed) {
      setReopenError(t('payroll.reopenDialog.reasonRequired'))
      return
    }

    setReopening(true)
    setReopenError(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/payroll/closings/{closing_id}/reopen',
        {
          params: { path: { closing_id: existingClosing.id } },
          body: { reopen_reason: trimmed },
        },
      )
      if (res.error || !res.data) {
        setReopenError(extractApiError(res.error, t('common.error')))
        return
      }
      setReopenOpen(false)
      setReopenReason('')
      await Promise.all([
        loadPeriodData(selectedStoreId, periodMonth),
        loadStoresAndHistory(),
      ])
    } finally {
      setReopening(false)
    }
  }

  const handleReopenKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isImeComposing(e)) {
      e.preventDefault()
      void handleReopenSubmit()
    }
  }

  const handleDownloadPdf = async (documentId: string) => {
    setGeneratingUrl(true)
    setSheetError(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/signed-url',
        {
          params: { path: { document_id: documentId } },
          body: { ttl_seconds: 60 },
        },
      )
      if (res.error || !res.data) {
        setSheetError(extractApiError(res.error, t('common.error')))
        return
      }
      setSignedUrlCountdown(res.data.ttl_seconds)
      window.open(res.data.url, '_blank', 'noopener,noreferrer')
    } finally {
      setGeneratingUrl(false)
    }
  }

  const columns = useMemo<ColumnDef<PayrollRowData>[]>(
    () => [
      {
        id: 'employee',
        header: t('payroll.columns.employee'),
        cell: ({ row }) => (
          <div>
            <div className="font-medium text-slate-900">
              {row.original.employee_full_name}
            </div>
            <div className="text-xs text-slate-500 tabular-nums">
              {row.original.employee_code}
            </div>
          </div>
        ),
      },
      {
        id: 'contract',
        header: t('payroll.columns.contract'),
        cell: ({ row }) => (
          <div className="text-xs text-slate-700">
            <div>{t(`employees.contractTypes.${row.original.contract_type}`)}</div>
            <div className="text-slate-500 tabular-nums">
              {t(`employees.scheduleTypes.${row.original.work_schedule_type}`)} ·{' '}
              {t('payroll.weeklyHoursUnit', {
                hours: formatHours(row.original.contracted_weekly_hours, locale),
              })}
            </div>
          </div>
        ),
      },
      {
        id: 'workedDays',
        header: t('payroll.columns.workedDays'),
        cell: ({ row }) => (
          <span className="text-sm font-medium text-slate-900 tabular-nums">
            {formatInt(row.original.worked_days, locale)}
          </span>
        ),
      },
      {
        id: 'hours',
        header: t('payroll.columns.hours'),
        cell: ({ row }) => (
          <div className="tabular-nums">
            <div className="text-sm font-semibold text-slate-900">
              {formatHours(row.original.total_net_hours, locale)} h
            </div>
            <div className="text-xs text-slate-500">
              {t('payroll.hoursBreakdown', {
                regular: formatHours(row.original.regular_hours, locale),
                overtime: formatHours(row.original.overtime_hours, locale),
              })}
            </div>
          </div>
        ),
      },
      {
        id: 'leaves',
        header: t('payroll.columns.leaves'),
        cell: ({ row }) => {
          const other =
            row.original.paid_leave_days + row.original.unpaid_leave_days
          return (
            <span className="text-xs text-slate-700 tabular-nums">
              {t('payroll.leavesSummary', {
                vacation: formatInt(row.original.vacation_days, locale),
                sick: formatInt(row.original.sick_leave_days, locale),
                other: formatInt(other, locale),
              })}
            </span>
          )
        },
      },
      {
        id: 'alerts',
        header: t('payroll.columns.alerts'),
        cell: ({ row }) => {
          const warnings = row.original.warning_alerts_count
          const violations = row.original.violation_alerts_count
          if (warnings === 0 && violations === 0) {
            return (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-600"
              >
                {t('payroll.alerts.clean')}
              </Badge>
            )
          }
          return (
            <div className="flex flex-wrap gap-1 tabular-nums">
              {violations > 0 && (
                <Badge
                  variant="outline"
                  className="border-rose-200 bg-rose-50 text-rose-700"
                >
                  {t('payroll.alerts.violations', { count: violations })}
                </Badge>
              )}
              {warnings > 0 && (
                <Badge
                  variant="outline"
                  className="border-amber-200 bg-amber-50 text-amber-700"
                >
                  {t('payroll.alerts.warnings', { count: warnings })}
                </Badge>
              )}
            </div>
          )
        },
      },
      {
        id: 'receipt',
        header: t('payroll.columns.receipt'),
        cell: ({ row }) => {
          if (!row.original.attendance_report_document_id) {
            return (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-600"
              >
                {t('payroll.receiptStatuses.preview')}
              </Badge>
            )
          }
          if (row.original.is_signed) {
            return (
              <div className="space-y-0.5">
                <Badge
                  variant="outline"
                  className="border-emerald-200 bg-emerald-50 text-emerald-700"
                >
                  <CheckCircle2 className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
                  {t('payroll.receiptStatuses.signed')}
                </Badge>
                {row.original.signed_at_utc && (
                  <div className="text-[11px] text-slate-500 tabular-nums">
                    {formatUtcDateTime(
                      row.original.signed_at_utc,
                      locale,
                      activeTimeZone,
                    )}
                  </div>
                )}
              </div>
            )
          }
          return (
            <Badge
              variant="outline"
              className="border-amber-200 bg-amber-50 text-amber-700"
            >
              {t('payroll.receiptStatuses.pending')}
            </Badge>
          )
        },
      },
    ],
    [activeTimeZone, locale, t],
  )

  const table = useReactTable({
    data: rows,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="min-w-0 space-y-4">
      {/* Header */}
      <div className="space-y-1">
        <h1 className="text-lg font-semibold tracking-tight text-slate-900">
          {t('payroll.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('payroll.subtitle')}</p>
      </div>

      {/* Compact Top Toolbar */}
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white p-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Select
            value={selectedStoreId}
            onValueChange={(v) => {
              setSelectedStoreId(v)
              setSelectedRow(null)
            }}
            disabled={stores.length === 0}
          >
            <SelectTrigger
              className="min-w-44 bg-white"
              aria-label={t('payroll.selectStore')}
            >
              <SelectValue placeholder={t('payroll.noStores')} />
            </SelectTrigger>
            <SelectContent>
              {stores.map((store) => (
                <SelectItem key={store.id} value={store.id}>
                  {store.code} · {store.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            type="month"
            aria-label={t('payroll.periodMonth')}
            value={periodMonth}
            onChange={(e) => {
              setPeriodMonth(e.target.value)
              setSelectedRow(null)
            }}
            className="w-auto min-w-40 bg-white tabular-nums"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {canCloseOrReopen && (
            <Button
              type="button"
              size="sm"
              className="bg-[#2563EB] text-white hover:bg-blue-700"
              disabled={
                loading ||
                closingSubmitting ||
                !preview?.can_close ||
                isClosed
              }
              onClick={() => void handleCloseMonth()}
            >
              <Lock className="h-4 w-4" aria-hidden="true" />
              {closingSubmitting
                ? t('payroll.actions.closing')
                : t('payroll.actions.closeMonth')}
            </Button>
          )}

          {canExportCsv && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!isClosed || exportingCsv}
              onClick={() => void handleExportCsv()}
            >
              <FileSpreadsheet className="h-4 w-4" aria-hidden="true" />
              {exportingCsv
                ? t('payroll.actions.exporting')
                : t('payroll.actions.exportCsv')}
            </Button>
          )}

          {canCloseOrReopen && isClosed && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setReopenReason('')
                setReopenError(null)
                setReopenOpen(true)
              }}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {t('payroll.actions.reopenMonth')}
            </Button>
          )}
        </div>
      </div>

      {/* Recent Closings Strip */}
      {recentClosings.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-slate-500">
            {t('payroll.recentClosings')}:
          </span>
          {recentClosings.slice(0, 6).map((item) => {
            const isSelected =
              item.closing.store_id === selectedStoreId &&
              item.closing.period_month === periodMonth
            return (
              <button
                key={item.closing.id}
                type="button"
                onClick={() => {
                  setSelectedStoreId(item.closing.store_id)
                  setPeriodMonth(item.closing.period_month)
                  setSelectedRow(null)
                }}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs transition-colors tabular-nums focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none ${
                  isSelected
                    ? 'border-[#2563EB] bg-white font-medium text-slate-900'
                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`}
              >
                <span>{item.store_code}</span>
                <span className="text-slate-400">·</span>
                <span>{item.closing.period_month}</span>
                <span className="text-slate-400">·</span>
                <span>v{item.closing.revision_number}</span>
                <Badge
                  variant="outline"
                  className={
                    item.closing.status === 'closed'
                      ? 'border-emerald-200 bg-emerald-50 px-1.5 py-0 text-[10px] text-emerald-700'
                      : 'border-amber-200 bg-amber-50 px-1.5 py-0 text-[10px] text-amber-700'
                  }
                >
                  {item.signed_count}/{item.total_reports}
                </Badge>
              </button>
            )
          })}
        </div>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* 4-Card KPI Strip */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {/* Card 1: Status & Revision */}
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-500">
              {t('payroll.kpi.statusLabel')}
            </span>
            <Lock className="h-4 w-4 text-slate-400" aria-hidden="true" />
          </div>
          <div className="mt-2">
            {isClosed && existingClosing ? (
              <Badge
                variant="outline"
                className="border-emerald-200 bg-emerald-50 text-emerald-700 tabular-nums"
              >
                {t('payroll.statuses.closed', {
                  revision: existingClosing.revision_number,
                })}
              </Badge>
            ) : isReopened && existingClosing ? (
              <Badge
                variant="outline"
                className="border-amber-200 bg-amber-50 text-amber-700 tabular-nums"
              >
                {t('payroll.statuses.reopened', {
                  revision: existingClosing.revision_number,
                })}
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-700"
              >
                {t('payroll.statuses.open')}
              </Badge>
            )}
          </div>
          <p className="mt-2 text-xs text-slate-500 tabular-nums">
            {isClosed && existingClosing
              ? t('payroll.kpi.statusClosedHint', {
                  time: formatUtcDateTime(
                    existingClosing.closed_at_utc,
                    locale,
                    activeTimeZone,
                  ),
                })
              : isReopened
                ? t('payroll.kpi.statusReopenedHint')
                : t('payroll.kpi.statusOpenHint')}
          </p>
        </div>

        {/* Card 2: Employees Included */}
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-500">
              {t('payroll.kpi.employeesLabel')}
            </span>
            <Users className="h-4 w-4 text-slate-400" aria-hidden="true" />
          </div>
          <div className="mt-1.5 text-2xl font-semibold text-slate-900 tabular-nums">
            {formatInt(kpis.employeeCount, locale)}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {t('payroll.kpi.employeesHint')}
          </p>
        </div>

        {/* Card 3: Net / Overtime Hours */}
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-500">
              {t('payroll.kpi.hoursLabel')}
            </span>
            <Clock className="h-4 w-4 text-slate-400" aria-hidden="true" />
          </div>
          <div className="mt-1.5 text-2xl font-semibold text-slate-900 tabular-nums">
            {formatHours(kpis.totalNet, locale)} h
          </div>
          <p className="mt-1 text-xs text-slate-500 tabular-nums">
            {t('payroll.kpi.hoursSub', {
              overtime: formatHours(kpis.totalOvertime, locale),
            })}
          </p>
        </div>

        {/* Card 4: SHA-256 Signature Progress OR Blocker Count */}
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-slate-500">
              {isClosed || kpis.totalReports > 0
                ? t('payroll.kpi.signaturesLabel')
                : t('payroll.kpi.blockersLabel')}
            </span>
            {isClosed || kpis.totalReports > 0 ? (
              <FileCheck2
                className="h-4 w-4 text-slate-400"
                aria-hidden="true"
              />
            ) : (
              <ShieldAlert
                className="h-4 w-4 text-slate-400"
                aria-hidden="true"
              />
            )}
          </div>

          {isClosed || kpis.totalReports > 0 ? (
            <>
              <div className="mt-1.5 text-2xl font-semibold text-slate-900 tabular-nums">
                {formatInt(kpis.signedCount, locale)} /{' '}
                {formatInt(kpis.totalReports, locale)}
              </div>
              <p className="mt-1 text-xs text-slate-500 tabular-nums">
                {t('payroll.kpi.signaturesSub', {
                  percent: formatInt(kpis.signaturePercent, locale),
                })}
              </p>
            </>
          ) : (
            <>
              <div className="mt-1.5 text-2xl font-semibold text-slate-900 tabular-nums">
                {formatInt(actionableBlockers.length, locale)}
              </div>
              <p className="mt-1 text-xs text-slate-500 tabular-nums">
                {actionableBlockers.length === 0
                  ? t('payroll.kpi.blockersClear')
                  : t('payroll.kpi.blockersPending', {
                      count: actionableBlockers.length,
                    })}
              </p>
            </>
          )}
        </div>
      </div>

      {/* Reopen Audit Banner */}
      {isReopened && existingClosing?.reopen_reason && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <div className="flex items-center gap-2 font-medium">
            <RotateCcw className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{t('payroll.banners.reopenAuditTitle')}</span>
          </div>
          <p className="mt-1 text-xs tabular-nums">
            {t('payroll.banners.reopenAuditMeta', {
              time: existingClosing.reopened_at_utc
                ? formatUtcDateTime(
                    existingClosing.reopened_at_utc,
                    locale,
                    activeTimeZone,
                  )
                : '-',
              reason: existingClosing.reopen_reason,
            })}
          </p>
        </div>
      )}

      {/* Readiness Blockers Banner */}
      {!isClosed && actionableBlockers.length > 0 && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span>{t('payroll.banners.blockersTitle')}</span>
            </div>
            {onNavigate && (
              <div className="flex flex-wrap gap-2">
                {actionableBlockers.some((b) => b.code === 'OPEN_SHIFT') && (
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    className="border-rose-200 bg-white text-rose-700 hover:bg-rose-50"
                    onClick={() => onNavigate('attendance')}
                  >
                    {t('payroll.banners.goToAttendance')}
                  </Button>
                )}
                {actionableBlockers.some((b) => b.code === 'PENDING_LEAVE') && (
                  <Button
                    type="button"
                    size="xs"
                    variant="outline"
                    className="border-rose-200 bg-white text-rose-700 hover:bg-rose-50"
                    onClick={() => onNavigate('leaves')}
                  >
                    {t('payroll.banners.goToLeaves')}
                  </Button>
                )}
              </div>
            )}
          </div>
          <ul className="mt-2 space-y-1 text-xs">
            {actionableBlockers.map((blocker, idx) => (
              <li
                key={`${blocker.code}-${blocker.reference_id ?? idx}`}
                className="tabular-nums"
              >
                <span className="font-medium">[{blocker.code}]</span>{' '}
                {blocker.employee_code ? `${blocker.employee_code} · ` : ''}
                {blocker.employee_full_name ? `${blocker.employee_full_name} — ` : ''}
                {blocker.local_date ? `${blocker.local_date}: ` : ''}
                {blocker.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Legal Alerts Summary Banner */}
      {(kpis.totalWarnings > 0 || kpis.totalViolations > 0) && (
        <div className="flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 tabular-nums">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {t('payroll.banners.alertsTitle', {
              warnings: kpis.totalWarnings,
              violations: kpis.totalViolations,
            })}
          </span>
        </div>
      )}

      {/* Full-Width TanStack Table */}
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        {loading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500">
            {t('payroll.empty')}
          </div>
        ) : (
          <Table>
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((header) => (
                    <TableHead key={header.id}>
                      {flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => (
                <TableRow
                  key={row.original.employee_id}
                  className="cursor-pointer hover:bg-slate-50"
                  onClick={() => {
                    setSelectedRow(row.original)
                    setSignedUrlCountdown(null)
                    setSheetError(null)
                  }}
                >
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id}>
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext(),
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Employee Monthly Detail Sheet */}
      <Sheet
        open={selectedRow !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedRow(null)
        }}
      >
        <SheetContent
          side="right"
          className="w-full overflow-y-auto bg-white sm:max-w-md"
        >
          {selectedRow && (
            <>
              <SheetHeader>
                <SheetTitle>{t('payroll.detailDrawer.title')}</SheetTitle>
                <SheetDescription>
                  {t('payroll.detailDrawer.description')}
                </SheetDescription>
              </SheetHeader>

              <div className="mt-4 space-y-4 px-4 text-sm">
                {sheetError && (
                  <div
                    role="alert"
                    className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                  >
                    {sheetError}
                  </div>
                )}

                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="font-semibold text-slate-900">
                    {selectedRow.employee_full_name}
                  </div>
                  <div className="mt-0.5 text-xs text-slate-600 tabular-nums">
                    {selectedRow.employee_code} · {periodMonth}
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <span className="text-slate-500">
                        {t('payroll.detailDrawer.nieDniMasked')}
                      </span>
                      <div className="font-mono font-medium text-slate-900 tabular-nums">
                        {selectedRow.nie_dni_masked ??
                          t('payroll.detailDrawer.notProvided')}
                      </div>
                    </div>
                    <div>
                      <span className="text-slate-500">
                        {t('payroll.detailDrawer.contractInfo')}
                      </span>
                      <div className="font-medium text-slate-900">
                        {t(
                          `employees.contractTypes.${selectedRow.contract_type}`,
                        )}{' '}
                        (
                        {t(
                          `employees.scheduleTypes.${selectedRow.work_schedule_type}`,
                        )}
                        )
                      </div>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* Hours Section */}
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                    {t('payroll.detailDrawer.hoursSection')}
                  </h3>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.workedDays')}
                      </div>
                      <div className="mt-0.5 text-base font-semibold text-slate-900 tabular-nums">
                        {formatInt(selectedRow.worked_days, locale)}
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.netHours')}
                      </div>
                      <div className="mt-0.5 text-base font-semibold text-slate-900 tabular-nums">
                        {formatHours(selectedRow.total_net_hours, locale)} h
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.regularHours')}
                      </div>
                      <div className="mt-0.5 font-medium text-slate-900 tabular-nums">
                        {formatHours(selectedRow.regular_hours, locale)} h
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.overtimeHours')}
                      </div>
                      <div className="mt-0.5 font-medium text-slate-900 tabular-nums">
                        {formatHours(selectedRow.overtime_hours, locale)} h
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.annualOvertime')}
                      </div>
                      <div className="mt-0.5 font-medium text-slate-900 tabular-nums">
                        {formatHours(selectedRow.annual_overtime_hours, locale)}{' '}
                        h
                      </div>
                    </div>
                    <div className="rounded-md border border-slate-200 p-2.5">
                      <div className="text-slate-500">
                        {t('payroll.detailDrawer.breakMinutes')}
                      </div>
                      <div className="mt-0.5 font-medium text-slate-900 tabular-nums">
                        {t('payroll.detailDrawer.minutesUnit', {
                          minutes: formatInt(
                            selectedRow.total_break_minutes,
                            locale,
                          ),
                        })}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Leaves Section */}
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                    {t('payroll.detailDrawer.leavesSection')}
                  </h3>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.vacationDays')}
                      </span>
                      <span className="font-semibold text-slate-900 tabular-nums">
                        {formatInt(selectedRow.vacation_days, locale)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.sickLeaveDays')}
                      </span>
                      <span className="font-semibold text-slate-900 tabular-nums">
                        {formatInt(selectedRow.sick_leave_days, locale)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.paidLeaveDays')}
                      </span>
                      <span className="font-semibold text-slate-900 tabular-nums">
                        {formatInt(selectedRow.paid_leave_days, locale)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.unpaidLeaveDays')}
                      </span>
                      <span className="font-semibold text-slate-900 tabular-nums">
                        {formatInt(selectedRow.unpaid_leave_days, locale)}
                      </span>
                    </div>
                  </div>
                </div>

                {/* Alerts Section */}
                <div className="space-y-2">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                    {t('payroll.detailDrawer.alertsSection')}
                  </h3>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.warningCount')}
                      </span>
                      <span className="font-semibold text-amber-700 tabular-nums">
                        {formatInt(selectedRow.warning_alerts_count, locale)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between rounded-md border border-slate-200 px-2.5 py-2">
                      <span className="text-slate-600">
                        {t('payroll.detailDrawer.violationCount')}
                      </span>
                      <span className="font-semibold text-rose-700 tabular-nums">
                        {formatInt(selectedRow.violation_alerts_count, locale)}
                      </span>
                    </div>
                  </div>
                </div>

                <Separator />

                {/* RDL 8/2019 PDF & SHA-256 Receipt Section */}
                <div className="space-y-2.5">
                  <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                    {t('payroll.detailDrawer.receiptSection')}
                  </h3>

                  {selectedRow.attendance_report_document_id ? (
                    <div className="space-y-2.5 rounded-md border border-slate-200 p-3 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-slate-500">
                          {t('payroll.columns.receipt')}
                        </span>
                        {selectedRow.is_signed ? (
                          <Badge
                            variant="outline"
                            className="border-emerald-200 bg-emerald-50 text-emerald-700"
                          >
                            {t('payroll.receiptStatuses.signed')}
                          </Badge>
                        ) : (
                          <Badge
                            variant="outline"
                            className="border-amber-200 bg-amber-50 text-amber-700"
                          >
                            {t('payroll.receiptStatuses.pending')}
                          </Badge>
                        )}
                      </div>

                      {selectedRow.signed_at_utc && (
                        <div>
                          <div className="text-slate-500">
                            {t('payroll.detailDrawer.signedAt')}
                          </div>
                          <div className="font-medium text-slate-900 tabular-nums">
                            {formatUtcDateTime(
                              selectedRow.signed_at_utc,
                              locale,
                              activeTimeZone,
                            )}
                          </div>
                        </div>
                      )}

                      {selectedRow.document_sha256_hex && (
                        <div>
                          <div className="text-slate-500">
                            {t('payroll.detailDrawer.sha256')}
                          </div>
                          <div className="mt-0.5 font-mono text-[11px] break-all text-slate-700 tabular-nums">
                            {selectedRow.document_sha256_hex}
                          </div>
                        </div>
                      )}

                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        className="w-full"
                        disabled={generatingUrl}
                        onClick={() =>
                          void handleDownloadPdf(
                            selectedRow.attendance_report_document_id!,
                          )
                        }
                      >
                        <Download className="h-4 w-4" aria-hidden="true" />
                        {generatingUrl
                          ? t('payroll.detailDrawer.generatingUrl')
                          : t('payroll.detailDrawer.downloadPdf')}
                      </Button>

                      {signedUrlCountdown !== null && (
                        <p className="text-[11px] text-slate-500 tabular-nums">
                          {signedUrlCountdown > 0
                            ? t('payroll.detailDrawer.signedUrlActive', {
                                seconds: signedUrlCountdown,
                              })
                            : t('payroll.detailDrawer.signedUrlExpired')}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
                      {t('payroll.detailDrawer.noPdfYet')}
                    </div>
                  )}
                </div>
              </div>

              <SheetFooter className="mt-6 px-4">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setSelectedRow(null)}
                >
                  {t('payroll.detailDrawer.close')}
                </Button>
              </SheetFooter>
            </>
          )}
        </SheetContent>
      </Sheet>

      {/* Reopen Month Dialog */}
      <AlertDialog open={reopenOpen} onOpenChange={setReopenOpen}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('payroll.reopenDialog.title')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('payroll.reopenDialog.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>

          <form
            onSubmit={(e) => void handleReopenSubmit(e)}
            className="space-y-4"
          >
            {reopenError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {reopenError}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="reopen-reason">
                {t('payroll.reopenDialog.reasonLabel')}
              </Label>
              <Input
                id="reopen-reason"
                value={reopenReason}
                maxLength={500}
                placeholder={t('payroll.reopenDialog.reasonPlaceholder')}
                onChange={(e) => setReopenReason(e.target.value)}
                onKeyDown={handleReopenKeyDown}
                required
              />
            </div>

            <AlertDialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={reopening}
                onClick={() => setReopenOpen(false)}
              >
                {t('payroll.reopenDialog.cancel')}
              </Button>
              <Button type="submit" disabled={reopening}>
                {reopening
                  ? t('payroll.actions.reopening')
                  : t('payroll.reopenDialog.confirm')}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
