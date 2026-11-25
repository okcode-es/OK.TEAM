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
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  EmployeeResponse,
  LeaveRequestResponse,
  LeaveStatus,
  LeaveType,
  StoreResponse,
  VacationBalanceResponse,
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

const LEAVE_TYPES: LeaveType[] = [
  'vacation',
  'sick_leave',
  'paid_leave',
  'unpaid_leave',
]

const STATUS_BADGE_STYLE: Record<LeaveStatus, string> = {
  pending: 'border-amber-200 bg-amber-50 text-amber-800',
  approved: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  rejected: 'border-rose-200 bg-rose-50 text-rose-700',
  cancelled: 'border-slate-200 bg-slate-100 text-slate-600',
}

function computeNaturalDaysPreview(startDate: string, endDate: string): number {
  if (!startDate || !endDate) return 0
  const start = new Date(`${startDate}T00:00:00Z`)
  const end = new Date(`${endDate}T00:00:00Z`)
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0
  if (end < start) return 0
  return Math.round((end.getTime() - start.getTime()) / 86_400_000) + 1
}

export function LeavesPage() {
  const { t, i18n } = useTranslation()
  const { activeTenant, profile } = useAuth()
  const locale = i18n.language || 'zh-CN'

  const role = activeTenant?.role
  const canReview =
    role === 'owner' || role === 'hr' || role === 'store_manager'
  const canCreate = role !== 'accountant'
  const isEmployeeRole = role === 'employee'

  const currentYear = new Date().getUTCFullYear()
  const yearOptions = [currentYear - 1, currentYear, currentYear + 1]

  const [year, setYear] = useState<number>(currentYear)
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [balanceEmployeeId, setBalanceEmployeeId] = useState<string>('')

  const [stores, setStores] = useState<StoreResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [requests, setRequests] = useState<LeaveRequestResponse[]>([])
  const [balance, setBalance] = useState<VacationBalanceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

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
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  // Detail & Review Sheet state
  const [selectedRequest, setSelectedRequest] =
    useState<LeaveRequestResponse | null>(null)
  const [reviewNote, setReviewNote] = useState<string>('')
  const [processingAction, setProcessingAction] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

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
      if (!isEmployeeRole && empRes.data.items.length > 0) {
        setBalanceEmployeeId((prev) =>
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
  }, [isEmployeeRole])

  const loadData = useCallback(async () => {
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

      const balancePromise =
        isEmployeeRole || balanceEmployeeId
          ? apiClient.GET('/api/v1/leaves/balance', {
              params: {
                query: {
                  year,
                  employee_id: isEmployeeRole ? undefined : balanceEmployeeId,
                },
              },
            })
          : Promise.resolve({ data: undefined, error: undefined })

      const [listRes, balRes] = await Promise.all([listPromise, balancePromise])
      if (listRes.error || !listRes.data) {
        setErrorBanner(extractApiError(listRes.error, t('common.error')))
      } else {
        setRequests(listRes.data.items)
      }

      if (balRes.data) {
        setBalance(balRes.data)
      } else if (!isEmployeeRole && !balanceEmployeeId) {
        setBalance(null)
      }
    } finally {
      setLoading(false)
    }
  }, [year, storeFilter, statusFilter, isEmployeeRole, balanceEmployeeId, t])

  useEffect(() => {
    setStoreFilter('all')
    setStatusFilter('all')
    setBalanceEmployeeId('')
    setSelectedRequest(null)
    void loadMetadata()
  }, [loadMetadata, activeTenant?.tenant_id])

  useEffect(() => {
    void loadData()
  }, [loadData, activeTenant?.tenant_id])

  const employeeMap = useMemo(() => {
    const map = new Map<string, EmployeeResponse>()
    for (const emp of employees) {
      map.set(emp.id, emp)
    }
    return map
  }, [employees])

  const naturalDaysPreview = useMemo(
    () => computeNaturalDaysPreview(createStartDate, createEndDate),
    [createStartDate, createEndDate],
  )

  const handleCreateSubmit = async (e?: FormEvent) => {
    if (e) e.preventDefault()
    if (!canCreate) return
    setCreating(true)
    setCreateError(null)
    try {
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
      setCreateOpen(false)
      setCreateReason('')
      await loadData()
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
          <Badge
            variant="outline"
            className="border-slate-200 bg-slate-50 text-slate-700"
          >
            {t(`leaves.types.${row.original.leave_type}`)}
          </Badge>
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
    data: requests,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  const canCancelSelected = useMemo(() => {
    if (!selectedRequest || selectedRequest.status !== 'pending') return false
    if (canReview) return true
    const emp = employeeMap.get(selectedRequest.employee_id)
    return Boolean(emp && emp.user_id && emp.user_id === profile?.user_id)
  }, [selectedRequest, canReview, employeeMap, profile?.user_id])

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">
            {t('leaves.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('leaves.subtitle')}</p>
        </div>
        {!canReview && (
          <Badge
            variant="outline"
            className="self-start border-slate-200 bg-white text-slate-600"
          >
            {t('leaves.readOnlyHint')}
          </Badge>
        )}
      </div>

      {/* Art. 38 ET Balance Strip */}
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={String(year)}
            onValueChange={(v) => setYear(Number(v))}
          >
            <SelectTrigger
              className="min-w-28 bg-white tabular-nums"
              aria-label={t('leaves.yearLabel')}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {yearOptions.map((y) => (
                <SelectItem key={y} value={String(y)}>
                  {y}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {!isEmployeeRole && employees.length > 0 && (
            <Select
              value={balanceEmployeeId}
              onValueChange={setBalanceEmployeeId}
            >
              <SelectTrigger
                className="min-w-52 bg-white"
                aria-label={t('leaves.employeeLabel')}
              >
                <SelectValue placeholder={t('leaves.employeeLabel')} />
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

        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
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
              {t('leaves.kpi.pending')}
            </div>
            <div className="mt-1 text-2xl font-semibold text-amber-700 tabular-nums">
              {balance ? balance.pending_days : '—'}{' '}
              <span className="text-xs font-normal text-slate-500">
                {t('leaves.kpi.daysUnit')}
              </span>
            </div>
          </div>
          <div className="rounded-md border border-slate-200 bg-white p-3.5">
            <div className="text-xs font-medium text-slate-500">
              {t('leaves.kpi.remaining')}
            </div>
            <div className="mt-1 text-2xl font-semibold text-teal-700 tabular-nums">
              {balance ? balance.remaining_days : '—'}{' '}
              <span className="text-xs font-normal text-slate-500">
                {t('leaves.kpi.daysUnit')}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Compact Toolbar */}
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

          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="min-w-36 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t('leaves.allStatuses')}</SelectItem>
              <SelectItem value="pending">
                {t('leaves.statuses.pending')}
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
            {t('leaves.count', { count: requests.length })}
          </span>
        </div>

        {canCreate && (
          <Button
            type="button"
            size="sm"
            onClick={() => {
              setCreateError(null)
              setCreateOpen(true)
            }}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('leaves.newRequest')}
          </Button>
        )}
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
        ) : requests.length === 0 ? (
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

      {/* Create Leave Request Sheet */}
      <Sheet open={createOpen} onOpenChange={setCreateOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto bg-white sm:max-w-md">
          <SheetHeader>
            <SheetTitle>{t('leaves.createDrawer.title')}</SheetTitle>
            <SheetDescription>
              {t('leaves.createDrawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            onSubmit={(e) => void handleCreateSubmit(e)}
            className="mt-4 space-y-4 px-4"
          >
            {createError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {createError}
              </div>
            )}

            {!isEmployeeRole && employees.length > 0 && (
              <div className="space-y-1.5">
                <Label htmlFor="leave-employee">
                  {t('leaves.createDrawer.employee')}
                </Label>
                <Select
                  value={createEmployeeId}
                  onValueChange={setCreateEmployeeId}
                >
                  <SelectTrigger id="leave-employee" className="w-full bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {employees.map((emp) => (
                      <SelectItem key={emp.id} value={emp.id}>
                        {emp.employee_code} · {emp.full_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="leave-type">
                {t('leaves.createDrawer.leaveType')}
              </Label>
              <Select
                value={createType}
                onValueChange={(v) => setCreateType(v as LeaveType)}
              >
                <SelectTrigger id="leave-type" className="w-full bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LEAVE_TYPES.map((lt) => (
                    <SelectItem key={lt} value={lt}>
                      {t(`leaves.types.${lt}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="leave-start">
                  {t('leaves.createDrawer.startDate')}
                </Label>
                <Input
                  id="leave-start"
                  type="date"
                  value={createStartDate}
                  onChange={(e) => setCreateStartDate(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="leave-end">
                  {t('leaves.createDrawer.endDate')}
                </Label>
                <Input
                  id="leave-end"
                  type="date"
                  value={createEndDate}
                  onChange={(e) => setCreateEndDate(e.target.value)}
                  required
                />
              </div>
            </div>

            <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-700 tabular-nums">
              {t('leaves.createDrawer.naturalDaysPreview', {
                days: naturalDaysPreview,
              })}
            </div>

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

            <Separator />

            <SheetFooter className="flex gap-2 px-0">
              <Button
                type="button"
                variant="outline"
                onClick={() => setCreateOpen(false)}
              >
                {t('leaves.createDrawer.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={creating || naturalDaysPreview <= 0}
              >
                {creating
                  ? t('leaves.createDrawer.submitting')
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

              {selectedRequest.status === 'pending' && canReview ? (
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
                {selectedRequest.status === 'pending' && canReview && (
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
    </div>
  )
}
