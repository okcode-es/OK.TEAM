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
  AlertCircle,
  CalendarClock,
  CalendarCog,
  CalendarDays,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  Clock,
  FileText,
  Paperclip,
  Plus,
  Upload,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  AttendancePage,
  type AttendanceNavigationIntent,
} from '@/features/attendance/AttendancePage'
import { EmployeeCombobox } from '@/components/EmployeeCombobox'
import { AssignLeaveSheet } from '@/features/leaves/AssignLeaveSheet'
import { LeaveEntitlementDialog } from '@/features/leaves/LeaveEntitlementDialog'
import { TeamCalendarGantt } from '@/features/leaves/TeamCalendarGantt'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  getAuthHeaders,
} from '@/api/client'
import type {
  CalendarHolidayResponse,
  EmployeeResponse,
  LeaveCalendarResponse,
  LeaveRequestResponse,
  LeaveStatus,
  LeaveType,
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
import { cn } from '@/lib/utils'

const PRIMARY_LEAVE_TYPES: LeaveType[] = [
  'vacation',
  'sick_leave',
  'medical_appointment',
  'day_off',
]

const ALL_LEAVE_TYPES: LeaveType[] = [
  'vacation',
  'sick_leave',
  'medical_appointment',
  'day_off',
  'paid_leave',
  'unpaid_leave',
]

const STATUS_BADGE_STYLE: Record<LeaveStatus, string> = {
  pending_manager: 'border-amber-200 bg-amber-50 text-amber-800',
  pending_hr: 'border-sky-200 bg-sky-50 text-sky-700',
  approved: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  rejected: 'border-rose-200 bg-rose-50 text-rose-700',
  cancelled: 'border-slate-200 bg-slate-100 text-slate-600',
}

function isPendingLeaveStatus(status: LeaveStatus): boolean {
  return status === 'pending_manager' || status === 'pending_hr'
}

function computeNaturalDaysPreview(startDate: string, endDate: string): number {
  if (!startDate || !endDate) return 0
  const start = new Date(`${startDate}T00:00:00Z`)
  const end = new Date(`${endDate}T00:00:00Z`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0
  if (end < start) return 0
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1
}

function toIsoDate(year: number, monthIndex: number, day: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function getMondayFirstCells(year: number, monthIndex: number): Array<number | null> {
  const firstDay = new Date(Date.UTC(year, monthIndex, 1)).getUTCDay()
  const offset = (firstDay + 6) % 7
  const daysInMonth = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate()
  const cells: Array<number | null> = []
  for (let i = 0; i < offset; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  return cells
}

export type TimeManagementTab = 'team' | 'absences' | 'attendance'

export function LeavesPage({
  initialTab = 'team',
  initialIntent = null,
  onConsumeIntent,
}: {
  initialTab?: TimeManagementTab
  initialIntent?: AttendanceNavigationIntent | null
  onConsumeIntent?: () => void
}) {
  const { activeTenant, profile } = useAuth()
  const scope = `${activeTenant?.tenant_id}:${activeTenant?.role}:${profile?.user_id}:${initialTab}`
  return (
    <LeavesWorkspace
      key={scope}
      initialTab={initialTab}
      initialIntent={initialIntent}
      onConsumeIntent={onConsumeIntent}
    />
  )
}

function LeavesWorkspace({
  initialTab = 'team',
  initialIntent = null,
  onConsumeIntent,
}: {
  initialTab?: TimeManagementTab
  initialIntent?: AttendanceNavigationIntent | null
  onConsumeIntent?: () => void
}) {
  const { t, i18n } = useTranslation()
  const { activeTenant, profile } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')

  const [activeTab, setActiveTab] = useState<TimeManagementTab>(initialTab)

  const role = activeTenant?.role
  const canReview =
    role === 'owner' || role === 'hr' || role === 'store_manager'
  const isAccountantRole = role === 'accountant'
  const canCreate = !isAccountantRole
  const isEmployeeRole = role === 'employee'
  const selfOnlyCalendar = isEmployeeRole || isAccountantRole

  const currentYear = new Date().getUTCFullYear()

  const [year, setYear] = useState<number>(currentYear)
  const [typeFilter, setTypeFilter] = useState<string>('all')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [employeeFilter, setEmployeeFilter] = useState<string>('all')
  const [calendarEmployeeId, setCalendarEmployeeId] = useState<string>('')

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [requests, setRequests] = useState<LeaveRequestResponse[]>([])
  const [calendarData, setCalendarData] = useState<LeaveCalendarResponse | null>(
    null,
  )
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const canViewAbsences =
    !isAccountantRole ||
    (profile != null &&
      employees.some(
        (emp) =>
          emp.user_id === profile.user_id &&
          (emp.employment_status === 'active' ||
            emp.employment_status === 'on_leave'),
      ))

  // Create Sheet state
  const [createOpen, setCreateOpen] = useState(false)
  const [createEmployeeId, setCreateEmployeeId] = useState<string>('')
  const [createType, setCreateType] = useState<LeaveType>('vacation')
  const [createStartDate, setCreateStartDate] = useState<string>(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [createEndDate, setCreateEndDate] = useState<string>(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [createReason, setCreateReason] = useState<string>('')
  const [createJustificanteFile, setCreateJustificanteFile] =
    useState<File | null>(null)
  const [pendingJustificanteLeaveId, setPendingJustificanteLeaveId] = useState<
    string | null
  >(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [submitAttempted, setSubmitAttempted] = useState(false)

  // Detail & Review Sheet state
  const [selectedRequest, setSelectedRequest] =
    useState<LeaveRequestResponse | null>(null)
  const canAccessJustificante =
    !isAccountantRole &&
    (selectedRequest?.leave_type === 'sick_leave' ||
      selectedRequest?.leave_type === 'medical_appointment' ||
      Boolean(selectedRequest?.medical_document_id))
  const [reviewNote, setReviewNote] = useState<string>('')
  const [processingAction, setProcessingAction] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [justificanteUploading, setJustificanteUploading] = useState(false)
  const [entitlementOpen, setEntitlementOpen] = useState(false)
  const [assignOpen, setAssignOpen] = useState(false)

  const loadMetadata = useCallback(async () => {
    const [storeRes, empRes] = await Promise.all([
      apiClient.GET('/api/v1/stores'),
      apiClient.GET('/api/v1/employees'),
    ])
    if (storeRes.data) setStores(storeRes.data.items)
    if (empRes.data) {
      setEmployees(empRes.data.items)
      if (!selfOnlyCalendar && empRes.data.items.length > 0) {
        setCalendarEmployeeId((prev) =>
          prev && empRes.data.items.some((e) => e.id === prev)
            ? prev
            : empRes.data.items[0].id,
        )
        setCreateEmployeeId((prev) =>
          prev && empRes.data.items.some((e) => e.id === prev)
            ? prev
            : empRes.data.items[0].id,
        )
      }
    }
  }, [selfOnlyCalendar])

  const loadData = useCallback(async () => {
    if (!canViewAbsences) return
    setLoading(true)
    setErrorBanner(null)
    try {
      const listPromise = apiClient.GET('/api/v1/leaves', {
        params: {
          query: {
            year,
            store_id: storeFilter !== 'all' ? storeFilter : undefined,
            status:
              statusFilter !== 'all'
                ? (statusFilter as LeaveStatus)
                : undefined,
          },
        },
      })

      const calendarPromise =
        selfOnlyCalendar || calendarEmployeeId
          ? apiClient.GET('/api/v1/leaves/calendar', {
              params: {
                query: {
                  year,
                  employee_id: selfOnlyCalendar ? undefined : calendarEmployeeId,
                },
              },
            })
          : Promise.resolve({ data: undefined, error: undefined })

      const [listRes, calRes] = await Promise.all([
        listPromise,
        calendarPromise,
      ])
      if (listRes.error || !listRes.data) {
        setErrorBanner(extractApiError(listRes.error, t('common.error')))
      } else {
        setRequests(listRes.data.items)
      }

      if (calRes.data) {
        setCalendarData(calRes.data)
      } else {
        setCalendarData(null)
      }
    } finally {
      setLoading(false)
    }
  }, [
    canViewAbsences,
    year,
    storeFilter,
    statusFilter,
    selfOnlyCalendar,
    calendarEmployeeId,
    t,
  ])

  useEffect(() => {
    setStoreFilter('all')
    setStatusFilter('all')
    setTypeFilter('all')
    setCalendarEmployeeId('')
    setSelectedRequest(null)
    void loadMetadata()
  }, [loadMetadata, activeTenant?.tenant_id])

  useEffect(() => {
    void loadData()
  }, [loadData, activeTenant?.tenant_id])

  const employeeMap = useMemo(() => {
    const map = new Map<string, EmployeeResponse>()
    for (const emp of employees) map.set(emp.id, emp)
    return map
  }, [employees])

  useEffect(() => {
    if (!initialIntent || initialTab === 'attendance') return
    setActiveTab('absences')
    if (initialIntent.employeeId && !selfOnlyCalendar) {
      setEmployeeFilter(initialIntent.employeeId)
      setCalendarEmployeeId(initialIntent.employeeId)
    }
    if (initialIntent.localDate) {
      const targetYear = Number(initialIntent.localDate.slice(0, 4))
      if (Number.isFinite(targetYear) && targetYear > 2000) {
        setYear(targetYear)
      }
    }
    onConsumeIntent?.()
  }, [initialIntent, initialTab, selfOnlyCalendar, onConsumeIntent])

  const filteredRequests = useMemo(() => {
    return requests.filter((r) => {
      if (typeFilter !== 'all' && r.leave_type !== typeFilter) return false
      if (employeeFilter !== 'all' && r.employee_id !== employeeFilter) {
        return false
      }
      return true
    })
  }, [requests, typeFilter, employeeFilter])

  const calendarHolidaysMap = useMemo(() => {
    const map = new Map<string, CalendarHolidayResponse>()
    for (const h of calendarData?.holidays ?? []) {
      map.set(h.date, h)
    }
    return map
  }, [calendarData?.holidays])

  const calendarLeavesByDay = useMemo(() => {
    const map = new Map<
      string,
      { status: LeaveStatus; leave_type: LeaveType; label: string }
    >()
    const items = calendarData?.leaves ?? []
    for (const lv of items) {
      if (lv.status === 'rejected' || lv.status === 'cancelled') continue
      if (typeFilter !== 'all' && lv.leave_type !== typeFilter) continue
      const cur = new Date(`${lv.start_date}T00:00:00Z`)
      const end = new Date(`${lv.end_date}T00:00:00Z`)
      while (cur <= end) {
        const key = cur.toISOString().slice(0, 10)
        map.set(key, {
          status: lv.status,
          leave_type: lv.leave_type,
          label: t(`leaves.types.${lv.leave_type}`),
        })
        cur.setUTCDate(cur.getUTCDate() + 1)
      }
    }
    return map
  }, [calendarData?.leaves, typeFilter, t])

  const weekdayHeaders = useMemo(() => {
    if (isEs) return ['L', 'M', 'X', 'J', 'V', 'S', 'D']
    return ['一', '二', '三', '四', '五', '六', '日']
  }, [isEs])

  const monthNames = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(locale, {
      month: 'long',
      timeZone: 'UTC',
    })
    return Array.from({ length: 12 }, (_, m) =>
      fmt.format(new Date(Date.UTC(year, m, 1))),
    )
  }, [locale, year])

  const naturalDaysPreview = useMemo(
    () => computeNaturalDaysPreview(createStartDate, createEndDate),
    [createStartDate, createEndDate],
  )

  const matchingBalance = useMemo(() => {
    if (!calendarData?.vacation_balance) return null
    if (isEmployeeRole) return calendarData.vacation_balance
    if (createEmployeeId && calendarData.employee_id === createEmployeeId) {
      return calendarData.vacation_balance
    }
    return null
  }, [calendarData, isEmployeeRole, createEmployeeId])

  const missingEmployee = !isEmployeeRole && !createEmployeeId
  const missingType = !createType
  const missingStartDate = !createStartDate
  const missingEndDate = !createEndDate
  const invalidDateOrder = Boolean(
    createStartDate && createEndDate && createEndDate < createStartDate,
  )
  const vacationExceeded = Boolean(
    createType === 'vacation' &&
      matchingBalance &&
      naturalDaysPreview > 0 &&
      naturalDaysPreview > matchingBalance.remaining_days,
  )

  const missingFieldLabels = useMemo(() => {
    const list: string[] = []
    if (missingEmployee) {
      list.push(t('leaves.createDrawer.validation.fieldEmployee'))
    }
    if (missingType) {
      list.push(t('leaves.createDrawer.validation.fieldType'))
    }
    if (missingStartDate) {
      list.push(t('leaves.createDrawer.validation.fieldStartDate'))
    }
    if (missingEndDate) {
      list.push(t('leaves.createDrawer.validation.fieldEndDate'))
    }
    return list
  }, [missingEmployee, missingType, missingStartDate, missingEndDate, t])

  const validationErrorItems = useMemo(() => {
    const items: string[] = []
    if (missingEmployee) {
      items.push(
        t('leaves.createDrawer.validation.requiredItem', {
          field: t('leaves.createDrawer.validation.fieldEmployee'),
        }),
      )
    }
    if (missingType) {
      items.push(
        t('leaves.createDrawer.validation.requiredItem', {
          field: t('leaves.createDrawer.validation.fieldType'),
        }),
      )
    }
    if (missingStartDate) {
      items.push(
        t('leaves.createDrawer.validation.requiredItem', {
          field: t('leaves.createDrawer.validation.fieldStartDate'),
        }),
      )
    }
    if (missingEndDate) {
      items.push(
        t('leaves.createDrawer.validation.requiredItem', {
          field: t('leaves.createDrawer.validation.fieldEndDate'),
        }),
      )
    }
    if (invalidDateOrder) {
      items.push(t('leaves.createDrawer.validation.endBeforeStart'))
    }
    return items
  }, [
    missingEmployee,
    missingType,
    missingStartDate,
    missingEndDate,
    invalidDateOrder,
    t,
  ])

  const hasValidationErrors = validationErrorItems.length > 0

  const uploadJustificanteFile = async (leaveId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    const headers = await getAuthHeaders()
    const response = await fetch(
      `${getApiBaseUrl()}/api/v1/leaves/${leaveId}/justificante`,
      {
        method: 'POST',
        headers,
        body: formData,
      },
    )
    if (!response.ok) {
      throw new Error(await extractFetchError(response, t('errors.GENERIC')))
    }
  }

  const handleCreateSubmit = async (e?: FormEvent) => {
    if (e) e.preventDefault()
    if (!canCreate) return
    setSubmitAttempted(true)
    setCreateError(null)
    if (hasValidationErrors) return
    setCreating(true)
    try {
      let leaveId = pendingJustificanteLeaveId
      if (!leaveId) {
        const res = await apiClient.POST('/api/v1/leaves', {
          body: {
            employee_id: isEmployeeRole ? undefined : createEmployeeId || undefined,
            leave_type: createType,
            start_date: createStartDate,
            end_date: createEndDate,
            reason: createReason.trim(),
          },
        })
        if (res.error || !res.data) {
          setCreateError(extractApiError(res.error, t('common.error')))
          return
        }
        leaveId = res.data.id
      }

      if (
        createJustificanteFile &&
        (createType === 'sick_leave' || createType === 'medical_appointment')
      ) {
        setPendingJustificanteLeaveId(leaveId)
        await uploadJustificanteFile(leaveId, createJustificanteFile)
      }

      setPendingJustificanteLeaveId(null)
      setCreateOpen(false)
      setSubmitAttempted(false)
      setCreateReason('')
      setCreateJustificanteFile(null)
      await loadData()
    } catch (err) {
      setCreateError(extractApiError(err, t('errors.GENERIC')))
    } finally {
      setCreating(false)
    }
  }

  const handleReview = async (approve: boolean) => {
    if (!selectedRequest || !canReview) return
    setProcessingAction(true)
    setDetailError(null)
    try {
      const res = await apiClient.POST('/api/v1/leaves/{leave_id}/review', {
        params: { path: { leave_id: selectedRequest.id } },
        body: {
          approve,
          review_note: reviewNote.trim(),
        },
      })
      if (res.error || !res.data) {
        setDetailError(extractApiError(res.error, t('common.error')))
        return
      }
      setSelectedRequest(null)
      await loadData()
    } finally {
      setProcessingAction(false)
    }
  }

  const handleCancelRequest = async () => {
    if (!selectedRequest) return
    setProcessingAction(true)
    setDetailError(null)
    try {
      const res = await apiClient.POST('/api/v1/leaves/{leave_id}/cancel', {
        params: { path: { leave_id: selectedRequest.id } },
      })
      if (res.error || !res.data) {
        setDetailError(extractApiError(res.error, t('common.error')))
        return
      }
      setSelectedRequest(null)
      await loadData()
    } finally {
      setProcessingAction(false)
    }
  }

  const handleDetailJustificanteUpload = async (file: File) => {
    if (!selectedRequest) return
    setJustificanteUploading(true)
    setDetailError(null)
    try {
      await uploadJustificanteFile(selectedRequest.id, file)
      await loadData()
      setSelectedRequest((prev) =>
        prev ? { ...prev, medical_document_id: 'uploaded' } : null,
      )
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setJustificanteUploading(false)
    }
  }

  const handlePreviewJustificante = async (leaveId: string) => {
    setDetailError(null)
    const res = await apiClient.POST(
      '/api/v1/leaves/{leave_id}/justificante/signed-url',
      {
        params: { path: { leave_id: leaveId } },
      },
    )
    if (res.error || !res.data) {
      setDetailError(extractApiError(res.error, t('common.error')))
      return
    }
    window.open(res.data.url, '_blank', 'noopener,noreferrer')
  }

  const handleCreateKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !isImeComposing(e)) {
      e.preventDefault()
      void handleCreateSubmit()
    }
  }

  const columns = useMemo<ColumnDef<LeaveRequestResponse>[]>(
    () => [
      {
        id: 'employee',
        header: t('leaves.columns.employee'),
        cell: ({ row }) => {
          const emp = employeeMap.get(row.original.employee_id)
          return (
            <div>
              <div className="font-medium text-slate-900">
                {emp?.full_name ?? row.original.employee_id.slice(0, 8)}
              </div>
              {emp && (
                <div className="text-xs text-slate-500 tabular-nums">
                  {emp.employee_code}
                </div>
              )}
            </div>
          )
        },
      },
      {
        id: 'type',
        header: t('leaves.columns.type'),
        cell: ({ row }) => (
          <div className="flex items-center gap-1.5">
            <Badge
              variant="outline"
              className="border-slate-200 bg-slate-50 text-slate-700"
            >
              {t(`leaves.types.${row.original.leave_type}`)}
            </Badge>
            {row.original.medical_document_id && (
              <Paperclip
                className="h-3.5 w-3.5 text-[#2563EB]"
                aria-label={t('leaves.justificante.attached')}
              />
            )}
          </div>
        ),
      },
      {
        id: 'dates',
        header: t('leaves.columns.dates'),
        cell: ({ row }) => (
          <span className="text-slate-700 tabular-nums">
            {row.original.start_date} — {row.original.end_date}
          </span>
        ),
      },
      {
        id: 'naturalDays',
        header: t('leaves.columns.naturalDays'),
        cell: ({ row }) => (
          <span className="font-medium text-slate-900 tabular-nums">
            {row.original.natural_days}
          </span>
        ),
      },
      {
        id: 'status',
        header: t('leaves.columns.status'),
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className={STATUS_BADGE_STYLE[row.original.status]}
          >
            {t(`leaves.statuses.${row.original.status}`)}
          </Badge>
        ),
      },
      {
        id: 'reason',
        header: t('leaves.columns.reason'),
        cell: ({ row }) => (
          <span className="block max-w-xs truncate text-slate-600">
            {row.original.reason || '—'}
          </span>
        ),
      },
    ],
    [employeeMap, t],
  )

  const table = useReactTable({
    data: filteredRequests,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  const canCancelSelected = useMemo(() => {
    if (
      isAccountantRole ||
      !selectedRequest ||
      !isPendingLeaveStatus(selectedRequest.status)
    ) {
      return false
    }
    if (canReview) return true
    const emp = employeeMap.get(selectedRequest.employee_id)
    return Boolean(emp && emp.user_id && emp.user_id === profile?.user_id)
  }, [isAccountantRole, selectedRequest, canReview, employeeMap, profile?.user_id])

  const canActOnSelectedStage = useMemo(() => {
    if (!selectedRequest) return false
    if (selectedRequest.status === 'pending_manager') {
      return role === 'store_manager' || role === 'owner'
    }
    if (selectedRequest.status === 'pending_hr') {
      return role === 'hr' || role === 'owner'
    }
    return false
  }, [selectedRequest, role])

  const balance = calendarData?.vacation_balance ?? null

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            {isEs ? 'Gestión del tiempo' : '时间管理'}
          </h1>
          <p className="text-sm text-slate-600">
            {activeTab === 'attendance'
              ? isEs
                ? 'Gestiona el calendario del equipo, las ausencias y el registro diario de jornada (RDL 8/2019).'
                : '统一管理团队日历、休假申请与符合西班牙 RDL 8/2019 的每日考勤记录。'
              : t('leaves.subtitle')}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {activeTab === 'absences' && (
            <div className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setYear((y) => y - 1)}
                aria-label={t('leaves.prevYear')}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </Button>
              <span className="px-2.5 text-sm font-semibold text-slate-900 tabular-nums">
                {year}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => setYear((y) => y + 1)}
                aria-label={t('leaves.nextYear')}
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          )}

          {canReview && activeTab !== 'attendance' && (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setEntitlementOpen(true)}
                className="border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              >
                <CalendarCog className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                {isEs ? 'Ajustar saldo de vacaciones' : '调整假期额度'}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={() => setAssignOpen(true)}
                className="border-blue-200 bg-blue-50/70 text-[#2563EB] hover:bg-blue-100/70"
              >
                <CalendarPlus className="h-4 w-4" aria-hidden="true" />
                {isEs ? 'Asignar ausencia' : '为员工安排假期'}
              </Button>
            </>
          )}

          {canCreate && (
            <Button
              type="button"
              size="sm"
              onClick={() => {
                setCreateError(null)
                setSubmitAttempted(false)
                setCreateJustificanteFile(null)
                setPendingJustificanteLeaveId(null)
                setCreateOpen(true)
              }}
              className="bg-[#2563EB] text-white hover:bg-blue-700"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              {isEs ? 'Solicitar ausencia' : '申请休假'}
            </Button>
          )}
        </div>
      </div>

      {/* Unified Time Management Tabs */}
      <div
        role="tablist"
        aria-label={isEs ? 'Gestión del tiempo' : '时间管理'}
        className="flex items-center gap-6 overflow-x-auto border-b border-slate-200"
      >
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'team'}
          onClick={() => setActiveTab('team')}
          className={cn(
            'inline-flex shrink-0 items-center gap-2 border-b-2 px-1 pb-2.5 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
            activeTab === 'team'
              ? 'border-[#2563EB] text-[#2563EB]'
              : 'border-transparent text-slate-600 hover:text-slate-900',
          )}
        >
          <CalendarDays className="h-4 w-4" aria-hidden="true" />
          <span>{isEs ? 'Calendario del equipo' : '团队日历'}</span>
        </button>
        {canViewAbsences && (
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'absences'}
            onClick={() => setActiveTab('absences')}
            className={cn(
              'inline-flex shrink-0 items-center gap-2 border-b-2 px-1 pb-2.5 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
              activeTab === 'absences'
                ? 'border-[#2563EB] text-[#2563EB]'
                : 'border-transparent text-slate-600 hover:text-slate-900',
            )}
          >
            <CalendarClock className="h-4 w-4" aria-hidden="true" />
            <span>{isEs ? 'Ausencia' : '假期'}</span>
          </button>
        )}
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'attendance'}
          onClick={() => setActiveTab('attendance')}
          className={cn(
            'inline-flex shrink-0 items-center gap-2 border-b-2 px-1 pb-2.5 text-sm font-medium whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
            activeTab === 'attendance'
              ? 'border-[#2563EB] text-[#2563EB]'
              : 'border-transparent text-slate-600 hover:text-slate-900',
          )}
        >
          <Clock className="h-4 w-4" aria-hidden="true" />
          <span>{isEs ? 'Registro de jornada' : '考勤记录'}</span>
        </button>
      </div>

      {activeTab === 'team' && <TeamCalendarGantt stores={stores} />}

      {activeTab === 'attendance' && (
        <AttendancePage
          embedded
          initialIntent={initialIntent}
          onConsumeIntent={onConsumeIntent}
        />
      )}

      {activeTab === 'absences' && (
        <>
          {/* Main Split: Left Vertical Type Switcher + Right KPI & 12-month Calendar */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* Left Type Switcher */}
        <aside className="space-y-2 lg:col-span-3">
          <div className="rounded-md border border-slate-200 bg-white p-2">
            <div className="mb-1.5 px-2 text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              {t('leaves.typeSwitcherTitle')}
            </div>
            <div className="flex flex-col gap-1">
              <button
                type="button"
                onClick={() => setTypeFilter('all')}
                className={cn(
                  'flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-xs font-medium transition-colors',
                  typeFilter === 'all'
                    ? 'bg-slate-900 text-white'
                    : 'text-slate-700 hover:bg-slate-100',
                )}
              >
                <span className="min-w-0 flex-1 truncate">{t('leaves.allTypes')}</span>
                <span className="shrink-0 text-[11px] opacity-80 tabular-nums">
                  {requests.length}
                </span>
              </button>

              {PRIMARY_LEAVE_TYPES.map((lt) => (
                <button
                  key={lt}
                  type="button"
                  onClick={() => setTypeFilter(lt)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-2 text-left text-xs font-medium transition-colors',
                    typeFilter === lt
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-700 hover:bg-slate-100',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{t(`leaves.types.${lt}`)}</span>
                  <span className="shrink-0 text-[11px] opacity-80 tabular-nums">
                    {calendarData?.type_counts?.[lt] ?? 0}
                  </span>
                </button>
              ))}

              <Separator className="my-1" />

              {(['paid_leave', 'unpaid_leave'] as LeaveType[]).map((lt) => (
                <button
                  key={lt}
                  type="button"
                  onClick={() => setTypeFilter(lt)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-xs transition-colors',
                    typeFilter === lt
                      ? 'bg-slate-900 text-white'
                      : 'text-slate-600 hover:bg-slate-100',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{t(`leaves.types.${lt}`)}</span>
                  <span className="shrink-0 text-[11px] opacity-80 tabular-nums">
                    {calendarData?.type_counts?.[lt] ?? 0}
                  </span>
                </button>
              ))}
            </div>
          </div>

          {!selfOnlyCalendar && employees.length > 0 && (
            <div className="rounded-md border border-slate-200 bg-white p-2.5">
              <Label className="mb-1.5 block text-[11px] text-slate-500">
                {t('leaves.employeeLabel')}
              </Label>
              <EmployeeCombobox
                employees={employees}
                value={calendarEmployeeId}
                onValueChange={setCalendarEmployeeId}
                ariaLabel={t('leaves.employeeLabel')}
                placeholder={t('leaves.employeeLabel')}
              />
            </div>
          )}
        </aside>

        {/* Right Content: 3 KPIs + 12-month Calendar */}
        <div className="space-y-4 lg:col-span-9">
          {/* 3 KPI Cards */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="rounded-md border border-slate-200 bg-white p-3.5">
              <div className="text-xs font-medium text-slate-500">
                {t('leaves.kpi.remaining')}
              </div>
              <div className="mt-1 text-2xl font-semibold text-[#2563EB] tabular-nums">
                {balance ? balance.remaining_days : '—'}{' '}
                <span className="text-xs font-normal text-slate-500">
                  {t('leaves.kpi.daysUnit')}
                </span>
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-white p-3.5">
              <div className="text-xs font-medium text-slate-500">
                {t('leaves.kpi.approved')}
              </div>
              <div className="mt-1 text-2xl font-semibold text-emerald-700 tabular-nums">
                {balance ? balance.approved_days : '—'}{' '}
                <span className="text-xs font-normal text-slate-500">
                  {t('leaves.kpi.daysUnit')}
                </span>
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-white p-3.5">
              <div className="text-xs font-medium text-slate-500">
                {t('leaves.kpi.entitled')}
              </div>
              <div className="mt-1 text-2xl font-semibold text-slate-900 tabular-nums">
                {balance ? balance.entitled_days : '—'}{' '}
                <span className="text-xs font-normal text-slate-500">
                  {t('leaves.kpi.daysUnit')}
                </span>
              </div>
            </div>
          </div>

          {/* 12-Month Full-Year Calendar */}
          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-semibold text-slate-900">
                {t('leaves.annualCalendarTitle', { year })}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-600">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-emerald-600" />
                  {t('leaves.legend.approved')}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-amber-400" />
                  {t('leaves.legend.pending')}
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-sm bg-rose-500" />
                  {t('leaves.legend.holiday')}
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {monthNames.map((monthLabel, monthIdx) => {
                const cells = getMondayFirstCells(year, monthIdx)
                return (
                  <div
                    key={monthIdx}
                    className="rounded-md border border-slate-200 bg-slate-50/40 p-2.5"
                  >
                    <div className="mb-1.5 text-xs font-semibold capitalize text-slate-900">
                      {monthLabel}
                    </div>
                    <div className="grid grid-cols-7 gap-0.5 text-center text-[10px] font-medium text-slate-400">
                      {weekdayHeaders.map((wd) => (
                        <span key={wd} className="py-0.5">
                          {wd}
                        </span>
                      ))}
                    </div>
                    <div className="mt-0.5 grid grid-cols-7 gap-0.5 text-center text-[11px] tabular-nums">
                      {cells.map((day, idx) => {
                        if (day === null) {
                          return <span key={`empty-${idx}`} className="h-6" />
                        }
                        const iso = toIsoDate(year, monthIdx, day)
                        const holiday = calendarHolidaysMap.get(iso)
                        const lv = calendarLeavesByDay.get(iso)

                        const titleParts: string[] = []
                        if (holiday) {
                          titleParts.push(
                            isEs ? holiday.name_es : holiday.name_zh,
                          )
                        }
                        if (lv) {
                          titleParts.push(
                            `${lv.label} (${t(`leaves.statuses.${lv.status}`)})`,
                          )
                        }

                        return (
                          <span
                            key={iso}
                            title={titleParts.join(' · ') || iso}
                            className={cn(
                              'flex h-6 items-center justify-center rounded-sm',
                              lv?.status === 'approved'
                                ? 'bg-emerald-600 font-medium text-white'
                                : lv?.status === 'pending_manager'
                                  ? 'bg-amber-300 font-medium text-slate-900'
                                  : lv?.status === 'pending_hr'
                                    ? 'bg-sky-200 font-medium text-slate-900'
                                    : holiday
                                    ? 'bg-rose-100 font-medium text-rose-700'
                                    : 'text-slate-700',
                            )}
                          >
                            {day}
                          </span>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Compact Toolbar for Requests Table */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-white p-3">
        <div className="flex flex-wrap items-center gap-2">
          {!isEmployeeRole && stores.length > 0 && (
            <Select value={storeFilter} onValueChange={setStoreFilter}>
              <SelectTrigger className="min-w-44 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t('leaves.allStores')}</SelectItem>
                {stores.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.code} · {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {!isEmployeeRole && employees.length > 0 && (
            <EmployeeCombobox
              employees={employees}
              value={employeeFilter}
              onValueChange={setEmployeeFilter}
              includeAllOption
              allValue="all"
              allowClear
              ariaLabel={t('leaves.employeeLabel')}
              className="min-w-52"
            />
          )}

          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="min-w-36 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('leaves.allStatuses')}</SelectItem>
              <SelectItem value="pending_manager">
                {t('leaves.statuses.pending_manager')}
              </SelectItem>
              <SelectItem value="pending_hr">
                {t('leaves.statuses.pending_hr')}
              </SelectItem>
              <SelectItem value="approved">
                {t('leaves.statuses.approved')}
              </SelectItem>
              <SelectItem value="rejected">
                {t('leaves.statuses.rejected')}
              </SelectItem>
              <SelectItem value="cancelled">
                {t('leaves.statuses.cancelled')}
              </SelectItem>
            </SelectContent>
          </Select>

          <span className="px-1 text-xs text-slate-500 tabular-nums">
            {t('leaves.count', { count: filteredRequests.length })}
          </span>
        </div>
      </div>

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* High-Density TanStack Table */}
      <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
        {loading ? (
          <div className="space-y-2 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : filteredRequests.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-500">
            {t('leaves.empty')}
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
                  key={row.id}
                  className="cursor-pointer hover:bg-slate-50"
                  onClick={() => {
                    setSelectedRequest(row.original)
                    setReviewNote(row.original.review_note || '')
                    setDetailError(null)
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
        </>
      )}

      {/* Create Leave Request Sheet */}
      <Sheet
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open)
          if (!open) {
            setSubmitAttempted(false)
            setCreateError(null)
            setPendingJustificanteLeaveId(null)
          }
        }}
      >
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t('leaves.createDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('leaves.createDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            noValidate
            onSubmit={(e) => void handleCreateSubmit(e)}
            className="mt-4 space-y-4 px-4"
          >
            {submitAttempted && hasValidationErrors && (
              <div
                role="alert"
                className="space-y-1 rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-700"
              >
                <div className="font-semibold">
                  {t('leaves.createDrawer.validation.summaryTitle')}
                </div>
                <ul className="list-inside list-disc space-y-0.5">
                  {validationErrorItems.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}

            {createError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {createError}
              </div>
            )}

            {missingFieldLabels.length > 0 && (
              <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <AlertCircle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                  aria-hidden="true"
                />
                <span>
                  {t('leaves.createDrawer.validation.missingGuidance', {
                    fields: missingFieldLabels.join(isEs ? ', ' : '、'),
                  })}
                </span>
              </div>
            )}

            {!isEmployeeRole && (
              <div className="space-y-1.5">
                <Label htmlFor="leave-employee">
                  {t('leaves.createDrawer.employee')}
                  <span className="ml-0.5 text-rose-500" aria-hidden="true">
                    *
                  </span>
                </Label>
                <EmployeeCombobox
                  id="leave-employee"
                  employees={employees}
                  value={createEmployeeId}
                  onValueChange={setCreateEmployeeId}
                  disabled={Boolean(pendingJustificanteLeaveId)}
                  placeholder={t(
                    'leaves.createDrawer.validation.selectPlaceholder',
                  )}
                  ariaLabel={t('leaves.createDrawer.employee')}
                  ariaInvalid={submitAttempted && missingEmployee}
                />
                {submitAttempted && missingEmployee && (
                  <p className="text-xs text-rose-600">
                    {t('leaves.createDrawer.validation.requiredItem', {
                      field: t('leaves.createDrawer.validation.fieldEmployee'),
                    })}
                  </p>
                )}
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="leave-type">
                {t('leaves.createDrawer.leaveType')}
                <span className="ml-0.5 text-rose-500" aria-hidden="true">
                  *
                </span>
              </Label>
              <Select
                value={createType}
                onValueChange={(v) => setCreateType(v as LeaveType)}
              >
                <SelectTrigger
                  id="leave-type"
                  aria-invalid={submitAttempted && missingType}
                  className={cn(
                    'w-full bg-white',
                    submitAttempted &&
                      missingType &&
                      'border-rose-400 focus-visible:ring-rose-500',
                  )}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ALL_LEAVE_TYPES.map((lt) => (
                    <SelectItem key={lt} value={lt}>
                      {t(`leaves.types.${lt}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {submitAttempted && missingType && (
                <p className="text-xs text-rose-600">
                  {t('leaves.createDrawer.validation.requiredItem', {
                    field: t('leaves.createDrawer.validation.fieldType'),
                  })}
                </p>
              )}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="leave-start">
                  {t('leaves.createDrawer.startDate')}
                  <span className="ml-0.5 text-rose-500" aria-hidden="true">
                    *
                  </span>
                </Label>
                <Input
                  id="leave-start"
                  type="date"
                  value={createStartDate}
                  onChange={(e) => setCreateStartDate(e.target.value)}
                  aria-invalid={submitAttempted && missingStartDate}
                  className={cn(
                    submitAttempted &&
                      missingStartDate &&
                      'border-rose-400 focus-visible:ring-rose-500',
                  )}
                />
                {submitAttempted && missingStartDate && (
                  <p className="text-xs text-rose-600">
                    {t('leaves.createDrawer.validation.requiredItem', {
                      field: t(
                        'leaves.createDrawer.validation.fieldStartDate',
                      ),
                    })}
                  </p>
                )}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="leave-end">
                  {t('leaves.createDrawer.endDate')}
                  <span className="ml-0.5 text-rose-500" aria-hidden="true">
                    *
                  </span>
                </Label>
                <Input
                  id="leave-end"
                  type="date"
                  value={createEndDate}
                  onChange={(e) => setCreateEndDate(e.target.value)}
                  aria-invalid={
                    submitAttempted && (missingEndDate || invalidDateOrder)
                  }
                  className={cn(
                    submitAttempted &&
                      (missingEndDate || invalidDateOrder) &&
                      'border-rose-400 focus-visible:ring-rose-500',
                  )}
                />
                {submitAttempted && missingEndDate && (
                  <p className="text-xs text-rose-600">
                    {t('leaves.createDrawer.validation.requiredItem', {
                      field: t('leaves.createDrawer.validation.fieldEndDate'),
                    })}
                  </p>
                )}
                {submitAttempted && !missingEndDate && invalidDateOrder && (
                  <p className="text-xs text-rose-600">
                    {t('leaves.createDrawer.validation.endBeforeStart')}
                  </p>
                )}
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 tabular-nums">
              {t('leaves.createDrawer.naturalDaysPreview', {
                days: naturalDaysPreview,
              })}
            </div>

            {vacationExceeded && matchingBalance && (
              <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 tabular-nums">
                <AlertCircle
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600"
                  aria-hidden="true"
                />
                <span>
                  {t('leaves.createDrawer.validation.vacationExceeded', {
                    requested: naturalDaysPreview,
                    remaining: matchingBalance.remaining_days,
                  })}
                </span>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="leave-reason">
                {t('leaves.createDrawer.reason')}
              </Label>
              <Input
                id="leave-reason"
                value={createReason}
                placeholder={t('leaves.createDrawer.reasonPlaceholder')}
                onChange={(e) => setCreateReason(e.target.value)}
                onKeyDown={handleCreateKeyDown}
              />
            </div>

            {(createType === 'sick_leave' ||
              createType === 'medical_appointment') && (
              <div className="space-y-1.5 rounded-md border border-slate-200 bg-slate-50 p-3">
                <Label htmlFor="leave-justificante" className="text-xs">
                  {t('leaves.justificante.uploadLabel')}
                </Label>
                <Input
                  id="leave-justificante"
                  type="file"
                  accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                  onChange={(e) =>
                    setCreateJustificanteFile(e.target.files?.[0] ?? null)
                  }
                />
                <p className="text-[11px] text-slate-500">
                  {t('leaves.justificante.hint')}
                </p>
              </div>
            )}

            <Separator />

            {pendingJustificanteLeaveId && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                {t('leaves.justificante.retryUploadHint')}
              </div>
            )}

            <SheetFooter className="flex gap-2 px-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setPendingJustificanteLeaveId(null)
                  setCreateOpen(false)
                }}
              >
                {t('leaves.createDrawer.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={creating}
              >
                {creating
                  ? t('leaves.createDrawer.submitting')
                  : pendingJustificanteLeaveId
                    ? t('leaves.justificante.retryUploadAction')
                    : t('leaves.createDrawer.submit')}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      {/* Detail & Review Sheet */}
      <Sheet
        open={Boolean(selectedRequest)}
        onOpenChange={(open) => {
          if (!open) setSelectedRequest(null)
        }}
      >
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t('leaves.detailDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('leaves.detailDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          {selectedRequest && (
            <div className="mt-4 space-y-4 px-4">
              {detailError && (
                <div
                  role="alert"
                  className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                >
                  {detailError}
                </div>
              )}

              <dl className="space-y-2.5 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-500">
                    {t('leaves.detailDrawer.employee')}
                  </dt>
                  <dd className="font-medium text-slate-900">
                    {employeeMap.get(selectedRequest.employee_id)?.full_name ??
                      selectedRequest.employee_id}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-500">
                    {t('leaves.detailDrawer.type')}
                  </dt>
                  <dd className="font-medium text-slate-900">
                    {t(`leaves.types.${selectedRequest.leave_type}`)}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-500">
                    {t('leaves.detailDrawer.dates')}
                  </dt>
                  <dd className="font-medium text-slate-900 tabular-nums">
                    {selectedRequest.start_date} — {selectedRequest.end_date}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-500">
                    {t('leaves.detailDrawer.naturalDays')}
                  </dt>
                  <dd className="font-medium text-slate-900 tabular-nums">
                    {selectedRequest.natural_days}
                  </dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-slate-500">
                    {t('leaves.detailDrawer.status')}
                  </dt>
                  <dd>
                    <Badge
                      variant="outline"
                      className={STATUS_BADGE_STYLE[selectedRequest.status]}
                    >
                      {t(`leaves.statuses.${selectedRequest.status}`)}
                    </Badge>
                  </dd>
                </div>
                {selectedRequest.reviewed_at_utc && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-slate-500">
                      {t('leaves.detailDrawer.reviewedAt')}
                    </dt>
                    <dd className="text-slate-700 tabular-nums">
                      {formatUtcDateTime(
                        selectedRequest.reviewed_at_utc,
                        locale,
                      )}
                    </dd>
                  </div>
                )}
              </dl>

              <div className="space-y-1">
                <Label>{t('leaves.detailDrawer.reason')}</Label>
                <p className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
                  {selectedRequest.reason || '—'}
                </p>
              </div>

              {/* Justificante Section */}
              {canAccessJustificante && (
                <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3">
                  <div className="flex items-center justify-between text-xs font-medium text-slate-700">
                    <span className="flex items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('leaves.justificante.title')}
                    </span>
                  </div>
                  {selectedRequest.medical_document_id && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        void handlePreviewJustificante(selectedRequest.id)
                      }
                      className="w-full justify-center bg-white text-xs"
                    >
                      <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('leaves.justificante.preview60s')}
                    </Button>
                  )}
                  <label className="flex cursor-pointer items-center justify-center gap-1.5 rounded-md border border-dashed border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">
                    <Upload className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>
                      {justificanteUploading
                        ? t('common.loading')
                        : t('leaves.justificante.uploadNew')}
                    </span>
                    <input
                      type="file"
                      className="hidden"
                      accept=".pdf,.jpg,.jpeg,.png,.webp,application/pdf,image/jpeg,image/png,image/webp"
                      disabled={justificanteUploading}
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) void handleDetailJustificanteUpload(file)
                      }}
                    />
                  </label>
                </div>
              )}

              {/* 2-Step Approval Timeline */}
              <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50/70 p-3 text-xs">
                <div className="font-semibold text-slate-700">
                  {t('leaves.detailDrawer.timeline.title')}
                </div>
                <div className="space-y-2">
                  {/* Step 1: Store Manager */}
                  <div className="rounded-md border border-slate-200 bg-white p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-slate-900">
                        {t('leaves.detailDrawer.timeline.step1')}
                      </span>
                      <Badge
                        variant="outline"
                        className={
                          selectedRequest.status === 'pending_manager'
                            ? 'border-amber-200 bg-amber-50 text-amber-800'
                            : selectedRequest.status === 'rejected' &&
                                !selectedRequest.hr_reviewed_at_utc
                              ? 'border-rose-200 bg-rose-50 text-rose-700'
                              : selectedRequest.manager_reviewed_at_utc
                                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                                : 'border-slate-200 bg-slate-100 text-slate-600'
                        }
                      >
                        {selectedRequest.status === 'pending_manager'
                          ? t('leaves.detailDrawer.timeline.waiting')
                          : selectedRequest.status === 'rejected' &&
                              !selectedRequest.hr_reviewed_at_utc
                            ? t('leaves.statuses.rejected')
                            : selectedRequest.manager_reviewed_at_utc
                              ? t('leaves.detailDrawer.timeline.passed')
                              : t('leaves.detailDrawer.timeline.skipped')}
                      </Badge>
                    </div>
                    {selectedRequest.manager_reviewed_at_utc && (
                      <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
                        {formatUtcDateTime(
                          selectedRequest.manager_reviewed_at_utc,
                          locale,
                        )}
                      </div>
                    )}
                    {selectedRequest.manager_review_note && (
                      <p className="mt-1 text-[11px] text-slate-700">
                        {selectedRequest.manager_review_note}
                      </p>
                    )}
                  </div>

                  {/* Step 2: HR Final Review */}
                  <div className="rounded-md border border-slate-200 bg-white p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-slate-900">
                        {t('leaves.detailDrawer.timeline.step2')}
                      </span>
                      <Badge
                        variant="outline"
                        className={
                          selectedRequest.status === 'pending_hr'
                            ? 'border-sky-200 bg-sky-50 text-sky-700'
                            : selectedRequest.status === 'approved'
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                              : selectedRequest.status === 'rejected' &&
                                  selectedRequest.hr_reviewed_at_utc
                                ? 'border-rose-200 bg-rose-50 text-rose-700'
                                : 'border-slate-200 bg-slate-100 text-slate-600'
                        }
                      >
                        {selectedRequest.status === 'pending_hr'
                          ? t('leaves.detailDrawer.timeline.waiting')
                          : selectedRequest.status === 'approved'
                            ? t('leaves.statuses.approved')
                            : selectedRequest.status === 'rejected' &&
                                selectedRequest.hr_reviewed_at_utc
                              ? t('leaves.statuses.rejected')
                              : t('leaves.detailDrawer.timeline.pendingPrev')}
                      </Badge>
                    </div>
                    {selectedRequest.hr_reviewed_at_utc && (
                      <div className="mt-1 text-[11px] text-slate-500 tabular-nums">
                        {formatUtcDateTime(
                          selectedRequest.hr_reviewed_at_utc,
                          locale,
                        )}
                      </div>
                    )}
                    {selectedRequest.hr_review_note && (
                      <p className="mt-1 text-[11px] text-slate-700">
                        {selectedRequest.hr_review_note}
                      </p>
                    )}
                  </div>
                </div>
              </div>

              {selectedRequest.status === 'pending_manager' && role === 'hr' && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                  {t('leaves.detailDrawer.hrWaitingManagerBanner')}
                </div>
              )}

              {selectedRequest.status === 'pending_hr' &&
                role === 'store_manager' && (
                  <div className="rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800">
                    {t('leaves.detailDrawer.managerWaitingHrBanner')}
                  </div>
                )}

              {canActOnSelectedStage ? (
                <div className="space-y-1.5">
                  <Label htmlFor="review-note">
                    {t('leaves.detailDrawer.reviewNote')}
                  </Label>
                  <Input
                    id="review-note"
                    value={reviewNote}
                    placeholder={t('leaves.detailDrawer.reviewNotePlaceholder')}
                    onChange={(e) => setReviewNote(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !isImeComposing(e)) {
                        e.preventDefault()
                      }
                    }}
                  />
                </div>
              ) : (
                selectedRequest.review_note && (
                  <div className="space-y-1">
                    <Label>{t('leaves.detailDrawer.reviewNote')}</Label>
                    <p className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
                      {selectedRequest.review_note}
                    </p>
                  </div>
                )
              )}

              <Separator />

              <SheetFooter className="flex flex-wrap gap-2 px-0">
                {canActOnSelectedStage && (
                  <>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={processingAction}
                      className="border-rose-200 text-rose-700 hover:bg-rose-50"
                      onClick={() => void handleReview(false)}
                    >
                      {t('leaves.detailDrawer.reject')}
                    </Button>
                    <Button
                      type="button"
                      disabled={processingAction}
                      onClick={() => void handleReview(true)}
                    >
                      {processingAction
                        ? t('leaves.detailDrawer.processing')
                        : selectedRequest.status === 'pending_hr'
                          ? t('leaves.detailDrawer.finalApprove')
                          : t('leaves.detailDrawer.approve')}
                    </Button>
                  </>
                )}

                {canCancelSelected && (
                  <Button
                    type="button"
                    variant="outline"
                    disabled={processingAction}
                    onClick={() => void handleCancelRequest()}
                  >
                    {t('leaves.detailDrawer.cancelRequest')}
                  </Button>
                )}

                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setSelectedRequest(null)}
                >
                  {t('leaves.detailDrawer.close')}
                </Button>
              </SheetFooter>
            </div>
          )}
        </SheetContent>
      </Sheet>

      <LeaveEntitlementDialog
        open={entitlementOpen}
        onOpenChange={setEntitlementOpen}
        employees={employees}
        stores={stores}
        defaultYear={year}
        onSuccess={loadData}
      />

      <AssignLeaveSheet
        open={assignOpen}
        onOpenChange={setAssignOpen}
        employees={employees}
        onSuccess={loadData}
      />
    </div>
  )
}
