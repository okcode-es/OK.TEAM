import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table'
import { Plus, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  ContractType,
  EmployeeResponse,
  EmploymentStatus,
  StoreResponse,
  WorkScheduleType,
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
import { EmployeeProfilePage } from '@/features/employees/EmployeeProfilePage'
import { isImeComposing } from '@/lib/ime'

const STATUS_BADGE_CLASS: Record<EmploymentStatus, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  on_leave: 'border-amber-200 bg-amber-50 text-amber-800',
  terminated: 'border-slate-200 bg-slate-100 text-slate-600',
  legal_hold: 'border-rose-200 bg-rose-50 text-rose-700',
  anonymized: 'border-slate-200 bg-slate-100 text-slate-500',
}

interface CreateEmployeeForm {
  primary_store_id: string
  employee_code: string
  full_name: string
  preferred_name: string
  email: string
  phone: string
  birth_date: string
  hire_date: string
  contract_type: ContractType
  work_schedule_type: WorkScheduleType
  contracted_weekly_hours: string
  annual_vacation_days: string
  nie_dni: string
  iban: string
  naf: string
}

const DEFAULT_CREATE_FORM: CreateEmployeeForm = {
  primary_store_id: '',
  employee_code: '',
  full_name: '',
  preferred_name: '',
  email: '',
  phone: '',
  birth_date: '',
  hire_date: new Date().toISOString().slice(0, 10),
  contract_type: 'indefinido',
  work_schedule_type: 'full_time',
  contracted_weekly_hours: '40.00',
  annual_vacation_days: '30',
  nie_dni: '',
  iban: '',
  naf: '',
}

export function EmployeesPage() {
  const { t } = useTranslation()
  const { activeTenant } = useAuth()
  const canManage =
    activeTenant?.role === 'owner' || activeTenant?.role === 'hr'

  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [searchQuery, setSearchQuery] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(
    null,
  )

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [form, setForm] = useState<CreateEmployeeForm>(DEFAULT_CREATE_FORM)
  const [saving, setSaving] = useState(false)
  const [drawerError, setDrawerError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const [empRes, storeRes] = await Promise.all([
        apiClient.GET('/api/v1/employees'),
        apiClient.GET('/api/v1/stores'),
      ])
      if (empRes.error || !empRes.data) {
        setErrorBanner(extractApiError(empRes.error, t('common.error')))
        return
      }
      setEmployees(empRes.data.items)
      if (storeRes.data) {
        setStores(storeRes.data.items)
      }
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    setSelectedEmployeeId(null)
    void loadData()
  }, [loadData, activeTenant?.tenant_id])

  const storeMap = useMemo(
    () => new Map(stores.map((s) => [s.id, s])),
    [stores],
  )

  const filteredEmployees = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return employees.filter((item) => {
      if (storeFilter !== 'all' && item.primary_store_id !== storeFilter) {
        return false
      }
      if (statusFilter !== 'all' && item.employment_status !== statusFilter) {
        return false
      }
      if (!q) return true
      return (
        item.full_name.toLowerCase().includes(q) ||
        item.preferred_name.toLowerCase().includes(q) ||
        item.employee_code.toLowerCase().includes(q) ||
        item.email.toLowerCase().includes(q)
      )
    })
  }, [employees, searchQuery, storeFilter, statusFilter])

  const openCreateDrawer = () => {
    setForm({
      ...DEFAULT_CREATE_FORM,
      primary_store_id: stores[0]?.id ?? '',
    })
    setDrawerError(null)
    setDrawerOpen(true)
  }

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canManage) return
    setSaving(true)
    setDrawerError(null)
    try {
      const { error } = await apiClient.POST('/api/v1/employees', {
        body: {
          primary_store_id: form.primary_store_id,
          employee_code: form.employee_code,
          full_name: form.full_name,
          preferred_name: form.preferred_name,
          email: form.email,
          phone: form.phone,
          birth_date: form.birth_date || null,
          hire_date: form.hire_date,
          contract_type: form.contract_type,
          work_schedule_type: form.work_schedule_type,
          contracted_weekly_hours: Number(form.contracted_weekly_hours),
          annual_vacation_days: Number(form.annual_vacation_days),
          nie_dni: form.nie_dni.trim() || null,
          iban: form.iban.trim() || null,
          naf: form.naf.trim() || null,
        },
      })
      if (error) {
        setDrawerError(extractApiError(error, t('common.error')))
        return
      }
      setDrawerOpen(false)
      await loadData()
    } finally {
      setSaving(false)
    }
  }

  const columns = useMemo<ColumnDef<EmployeeResponse>[]>(
    () => [
      {
        accessorKey: 'employee_code',
        header: t('employees.columns.code'),
        cell: ({ row }) => (
          <span className="font-mono text-xs font-medium text-slate-700 tabular-nums">
            {row.original.employee_code}
          </span>
        ),
      },
      {
        accessorKey: 'full_name',
        header: t('employees.columns.employee'),
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium text-slate-900">
              {row.original.full_name}
              {row.original.preferred_name ? (
                <span className="ml-1.5 text-xs font-normal text-slate-500">
                  ({row.original.preferred_name})
                </span>
              ) : null}
            </span>
            <span className="text-xs text-slate-500">
              {row.original.email || '—'}
            </span>
          </div>
        ),
      },
      {
        accessorKey: 'primary_store_id',
        header: t('employees.columns.store'),
        cell: ({ row }) => {
          const st = storeMap.get(row.original.primary_store_id)
          return (
            <span className="text-slate-700 tabular-nums">
              {st ? `${st.code} · ${st.name}` : '—'}
            </span>
          )
        },
      },
      {
        accessorKey: 'work_schedule_type',
        header: t('employees.columns.schedule'),
        cell: ({ row }) => (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-xs font-medium text-slate-700 tabular-nums">
              {row.original.contracted_weekly_hours} h
            </span>
            {row.original.is_overtime_prohibited ? (
              <Badge
                variant="outline"
                className="border-amber-200 bg-amber-50 text-amber-800"
              >
                {t('employees.overtimeProhibited')}
              </Badge>
            ) : (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-50 text-slate-600"
              >
                {t(`employees.scheduleTypes.${row.original.work_schedule_type}`)}
              </Badge>
            )}
          </div>
        ),
      },
      {
        accessorKey: 'nie_dni_masked',
        header: t('employees.columns.nieMasked'),
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600 tabular-nums">
            {row.original.nie_dni_masked ?? '—'}
          </span>
        ),
      },
      {
        accessorKey: 'employment_status',
        header: t('employees.columns.status'),
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className={STATUS_BADGE_CLASS[row.original.employment_status]}
          >
            {t(
              `employees.employmentStatuses.${row.original.employment_status}`,
            )}
          </Badge>
        ),
      },
    ],
    [t, storeMap],
  )

  const table = useReactTable({
    data: filteredEmployees,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  if (selectedEmployeeId) {
    return (
      <EmployeeProfilePage
        employeeId={selectedEmployeeId}
        stores={stores}
        onBack={() => {
          setSelectedEmployeeId(null)
          void loadData()
        }}
      />
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h1
            id="employees-table-heading"
            className="text-lg font-semibold text-slate-900"
          >
            {t('employees.title')}
          </h1>
          <p className="text-sm text-slate-600">{t('employees.subtitle')}</p>
        </div>

        {canManage && (
          <Button
            type="button"
            disabled={stores.length === 0}
            onClick={openCreateDrawer}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            {t('employees.addButton')}
          </Button>
        )}
      </div>

      {!canManage && (
        <div className="rounded-md border border-slate-200 bg-white px-3.5 py-2.5 text-xs text-slate-600">
          {t('employees.readOnlyHint')}
        </div>
      )}

      {errorBanner && (
        <div
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      <section className="overflow-hidden rounded-md border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-slate-200 px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-52 sm:w-64">
              <Search
                className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-slate-400"
                aria-hidden="true"
              />
              <Input
                type="search"
                aria-label={t('employees.searchPlaceholder')}
                enterKeyHint="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) {
                    e.preventDefault()
                  }
                }}
                placeholder={t('employees.searchPlaceholder')}
                className="h-8 pl-8 text-sm"
              />
            </div>

            <Select value={storeFilter} onValueChange={setStoreFilter}>
              <SelectTrigger className="h-8 min-w-36 bg-white text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t('employees.filterStore')}</SelectItem>
                {stores.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.code} · {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="h-8 min-w-36 bg-white text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">
                  {t('employees.filterStatus')}
                </SelectItem>
                {(
                  [
                    'active',
                    'on_leave',
                    'legal_hold',
                    'anonymized',
                  ] as EmploymentStatus[]
                ).map((st) => (
                  <SelectItem key={st} value={st}>
                    {t(`employees.employmentStatuses.${st}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <span className="text-xs text-slate-500 tabular-nums">
            {t('employees.count', { count: filteredEmployees.length })}
          </span>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="employees-table-heading"
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
                    {t('employees.empty')}
                  </TableCell>
                </TableRow>
              ) : (
                table.getRowModel().rows.map((row) => (
                  <TableRow
                    key={row.id}
                    onClick={() => setSelectedEmployeeId(row.original.id)}
                    className="cursor-pointer border-slate-200 hover:bg-slate-50"
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell
                        key={cell.id}
                        className="px-4 py-2.5 text-sm whitespace-nowrap"
                      >
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

      {/* Right Slide-Over Create Drawer */}
      <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
        <SheetContent
          side="right"
          className="flex w-full flex-col overscroll-contain bg-white sm:max-w-lg"
        >
          <SheetHeader className="border-b border-slate-200 px-6 py-4">
            <SheetTitle>{t('employees.drawer.createTitle')}</SheetTitle>
            <SheetDescription>
              {t('employees.drawer.description')}
            </SheetDescription>
          </SheetHeader>

          <form
            id="employee-create-form"
            onSubmit={handleCreate}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && isImeComposing(e)) {
                e.preventDefault()
              }
            }}
            className="flex-1 space-y-4 overflow-y-auto px-6 py-4"
          >
            {drawerError && (
              <div
                role="alert"
                className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
              >
                {drawerError}
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="emp-code">{t('employees.fields.code')}</Label>
                <Input
                  id="emp-code"
                  required
                  spellCheck={false}
                  className="uppercase tabular-nums"
                  value={form.employee_code}
                  onChange={(e) =>
                    setForm({ ...form, employee_code: e.target.value })
                  }
                  placeholder="EMP-001"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-store">
                  {t('employees.fields.primaryStore')}
                </Label>
                <Select
                  value={form.primary_store_id}
                  onValueChange={(val) =>
                    setForm({ ...form, primary_store_id: val })
                  }
                >
                  <SelectTrigger id="emp-store" className="w-full">
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
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="emp-fullname">
                  {t('employees.fields.fullName')}
                </Label>
                <Input
                  id="emp-fullname"
                  required
                  value={form.full_name}
                  onChange={(e) =>
                    setForm({ ...form, full_name: e.target.value })
                  }
                  placeholder="Xiaohong Lin"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-preferred">
                  {t('employees.fields.preferredName')}
                </Label>
                <Input
                  id="emp-preferred"
                  value={form.preferred_name}
                  onChange={(e) =>
                    setForm({ ...form, preferred_name: e.target.value })
                  }
                  placeholder="林小红"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="emp-email">{t('employees.fields.email')}</Label>
                <Input
                  id="emp-email"
                  type="email"
                  spellCheck={false}
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-hire">
                  {t('employees.fields.hireDate')}
                </Label>
                <Input
                  id="emp-hire"
                  type="date"
                  required
                  className="tabular-nums"
                  value={form.hire_date}
                  onChange={(e) =>
                    setForm({ ...form, hire_date: e.target.value })
                  }
                />
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="emp-contract">
                  {t('employees.fields.contractType')}
                </Label>
                <Select
                  value={form.contract_type}
                  onValueChange={(val) =>
                    setForm({ ...form, contract_type: val as ContractType })
                  }
                >
                  <SelectTrigger id="emp-contract" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(
                      [
                        'indefinido',
                        'temporal',
                        'fijo_discontinuo',
                        'practicas',
                      ] as ContractType[]
                    ).map((ct) => (
                      <SelectItem key={ct} value={ct}>
                        {t(`employees.contractTypes.${ct}`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="emp-schedule">
                  {t('employees.fields.scheduleType')}
                </Label>
                <Select
                  value={form.work_schedule_type}
                  onValueChange={(val) => {
                    const nextSchedule = val as WorkScheduleType
                    setForm({
                      ...form,
                      work_schedule_type: nextSchedule,
                      contracted_weekly_hours:
                        nextSchedule === 'part_time' ? '20.00' : '40.00',
                    })
                  }}
                >
                  <SelectTrigger id="emp-schedule" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="full_time">
                      {t('employees.scheduleTypes.full_time')}
                    </SelectItem>
                    <SelectItem value="part_time">
                      {t('employees.scheduleTypes.part_time')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="emp-hours">
                  {t('employees.fields.weeklyHours')}
                </Label>
                <Input
                  id="emp-hours"
                  type="number"
                  step="0.5"
                  min="1"
                  max="40"
                  required
                  className="tabular-nums"
                  value={form.contracted_weekly_hours}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      contracted_weekly_hours: e.target.value,
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-vacation">
                  {t('employees.fields.vacationDays')}
                </Label>
                <Input
                  id="emp-vacation"
                  type="number"
                  min="30"
                  max="90"
                  required
                  className="tabular-nums"
                  value={form.annual_vacation_days}
                  onChange={(e) =>
                    setForm({ ...form, annual_vacation_days: e.target.value })
                  }
                />
              </div>
            </div>

            <div className="space-y-3 rounded-md border border-slate-200 bg-slate-50 p-3.5">
              <div className="space-y-1.5">
                <Label htmlFor="emp-nie">{t('employees.fields.nieDni')}</Label>
                <Input
                  id="emp-nie"
                  spellCheck={false}
                  autoComplete="off"
                  className="bg-white font-mono uppercase tabular-nums"
                  value={form.nie_dni}
                  onChange={(e) =>
                    setForm({ ...form, nie_dni: e.target.value })
                  }
                  placeholder="Y1234567X"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-iban">{t('employees.fields.iban')}</Label>
                <Input
                  id="emp-iban"
                  spellCheck={false}
                  autoComplete="off"
                  className="bg-white font-mono uppercase tabular-nums"
                  value={form.iban}
                  onChange={(e) => setForm({ ...form, iban: e.target.value })}
                  placeholder="ES9121000418450200051332"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-naf">{t('employees.fields.naf')}</Label>
                <Input
                  id="emp-naf"
                  spellCheck={false}
                  autoComplete="off"
                  className="bg-white font-mono tabular-nums"
                  value={form.naf}
                  onChange={(e) => setForm({ ...form, naf: e.target.value })}
                  placeholder="281234567840"
                />
              </div>
            </div>
          </form>

          <SheetFooter className="flex-row items-center justify-end gap-2 border-t border-slate-200 px-6 py-4">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDrawerOpen(false)}
            >
              {t('employees.drawer.cancel')}
            </Button>
            <Button
              type="submit"
              form="employee-create-form"
              disabled={saving}
            >
              {saving
                ? t('employees.drawer.saving')
                : t('employees.drawer.save')}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  )
}
