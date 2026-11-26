import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type VisibilityState,
} from '@tanstack/react-table'
import {
  Briefcase,
  Building2,
  Columns3,
  Download,
  Filter,
  Hash,
  LayoutGrid,
  Network,
  Plus,
  Search,
  Shield,
  Store as StoreIcon,
  Table2,
  Upload,
  User,
  Users,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  ContractType,
  EmployeeResponse,
  EmploymentStatus,
  StoreResponse,
  WorkScheduleType,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
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
import { BulkImportSheet } from '@/features/employees/BulkImportSheet'
import { EmployeeProfilePage } from '@/features/employees/EmployeeProfilePage'
import { OrgChartView } from '@/features/employees/OrgChartView'
import { isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

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
  job_title: string
  department: string
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
  job_title: '',
  department: '',
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
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')
  const canManage =
    activeTenant?.role === 'owner' || activeTenant?.role === 'hr'

  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)

  const [primaryTab, setPrimaryTab] = useState<'list' | 'org'>('list')
  const [viewMode, setViewMode] = useState<'table' | 'card'>('table')
  const [searchQuery, setSearchQuery] = useState('')
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [departmentFilter, setDepartmentFilter] = useState<string>('all')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [contractFilter, setContractFilter] = useState<string>('all')

  const [filterPanelOpen, setFilterPanelOpen] = useState(false)
  const [columnsPanelOpen, setColumnsPanelOpen] = useState(false)
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    employee_code: true,
    full_name: true,
    job_title: true,
    primary_store_id: true,
    work_schedule_type: true,
    nie_dni_masked: true,
    employment_status: true,
  })

  const [selectedEmployeeId, setSelectedEmployeeId] = useState<string | null>(
    null,
  )

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [bulkImportOpen, setBulkImportOpen] = useState(false)
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

  const departments = useMemo(() => {
    const set = new Set<string>()
    for (const e of employees) {
      if (e.department?.trim()) set.add(e.department.trim())
    }
    return Array.from(set).sort()
  }, [employees])

  const filteredEmployees = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return employees.filter((item) => {
      if (storeFilter !== 'all' && item.primary_store_id !== storeFilter) {
        return false
      }
      if (
        departmentFilter !== 'all' &&
        (item.department?.trim() || '') !== departmentFilter
      ) {
        return false
      }
      if (statusFilter !== 'all' && item.employment_status !== statusFilter) {
        return false
      }
      if (contractFilter !== 'all' && item.contract_type !== contractFilter) {
        return false
      }
      if (!q) return true
      return (
        item.full_name.toLowerCase().includes(q) ||
        item.preferred_name.toLowerCase().includes(q) ||
        item.employee_code.toLowerCase().includes(q) ||
        item.email.toLowerCase().includes(q) ||
        (item.job_title ?? '').toLowerCase().includes(q) ||
        (item.department ?? '').toLowerCase().includes(q)
      )
    })
  }, [
    employees,
    searchQuery,
    storeFilter,
    departmentFilter,
    statusFilter,
    contractFilter,
  ])

  const activeFiltersCount =
    (storeFilter !== 'all' ? 1 : 0) +
    (departmentFilter !== 'all' ? 1 : 0) +
    (statusFilter !== 'all' ? 1 : 0) +
    (contractFilter !== 'all' ? 1 : 0)

  const clearAllFilters = () => {
    setStoreFilter('all')
    setDepartmentFilter('all')
    setStatusFilter('all')
    setContractFilter('all')
    setSearchQuery('')
  }

  const handleExportCsv = () => {
    const headers = [
      'Code',
      'Full Name',
      'Preferred Name',
      'Job Title',
      'Department',
      'Email',
      'Store',
      'Contract Type',
      'Weekly Hours',
      'Status',
    ]
    const rows = filteredEmployees.map((e) => {
      const st = storeMap.get(e.primary_store_id)
      return [
        e.employee_code,
        e.full_name,
        e.preferred_name,
        e.job_title ?? '',
        e.department ?? '',
        e.email,
        st ? `${st.code} ${st.name}` : '',
        e.contract_type,
        String(e.contracted_weekly_hours),
        e.employment_status,
      ]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`)
        .join(',')
    })
    const csv = `\uFEFF${headers.join(',')}\n${rows.join('\n')}`
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `okteam-employees-${new Date().toISOString().slice(0, 10)}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

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
          job_title: form.job_title.trim(),
          department: form.department.trim(),
          emergency_contact_name: '',
          emergency_contact_phone: '',
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
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <Hash className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            {t('employees.columns.code')}
          </span>
        ),
        cell: ({ row }) => (
          <span className="font-mono text-xs font-medium text-slate-700 tabular-nums">
            {row.original.employee_code}
          </span>
        ),
      },
      {
        accessorKey: 'full_name',
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <User className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            {t('employees.columns.employee')}
          </span>
        ),
        cell: ({ row }) => (
          <div className="flex items-center gap-2.5">
            <UserAvatar
              avatarUrl={row.original.avatar_url}
              name={row.original.preferred_name || row.original.full_name}
              size="sm"
            />
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
          </div>
        ),
      },
      {
        accessorKey: 'job_title',
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <Briefcase
              className="h-3.5 w-3.5 text-slate-400"
              aria-hidden="true"
            />
            {isEs ? 'Puesto / Dpto.' : '岗位 / 部门'}
          </span>
        ),
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="text-xs font-medium text-slate-800">
              {row.original.job_title || '—'}
            </span>
            <span className="text-[11px] text-slate-500">
              {row.original.department || '—'}
            </span>
          </div>
        ),
      },
      {
        accessorKey: 'primary_store_id',
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <StoreIcon
              className="h-3.5 w-3.5 text-slate-400"
              aria-hidden="true"
            />
            {t('employees.columns.store')}
          </span>
        ),
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
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <Building2
              className="h-3.5 w-3.5 text-slate-400"
              aria-hidden="true"
            />
            {t('employees.columns.schedule')}
          </span>
        ),
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
        header: () => (
          <span className="inline-flex items-center gap-1.5">
            <Shield
              className="h-3.5 w-3.5 text-slate-400"
              aria-hidden="true"
            />
            {t('employees.columns.nieMasked')}
          </span>
        ),
        cell: ({ row }) => (
          <span className="font-mono text-xs text-slate-600 tabular-nums">
            {row.original.nie_dni_masked ?? '—'}
          </span>
        ),
      },
      {
        accessorKey: 'employment_status',
        header: () => t('employees.columns.status'),
        cell: ({ row }) => (
          <Badge
            variant="outline"
            className={STATUS_BADGE_CLASS[row.original.employment_status]}
          >
            {t(`employees.employmentStatuses.${row.original.employment_status}`)}
          </Badge>
        ),
      },
    ],
    [storeMap, t, isEs],
  )

  const table = useReactTable({
    data: filteredEmployees,
    columns,
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
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
      {/* Header + Primary Tabs */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            {isEs ? 'Organización' : '组织与员工'}
          </h1>
          <p className="text-sm text-slate-600">{t('employees.subtitle')}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleExportCsv}
            className="bg-white"
          >
            <Download className="h-4 w-4" aria-hidden="true" />
            {isEs ? 'Exportar CSV' : '导出 CSV'}
          </Button>
          {canManage && (
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setBulkImportOpen(true)}
                className="bg-white"
              >
                <Upload className="h-4 w-4" aria-hidden="true" />
                {t('employees.bulkImport.button')}
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={openCreateDrawer}
                className="bg-[#2563EB] text-white hover:bg-blue-700"
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                {t('employees.addButton')}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Top Primary Tabs: Personal | Organigrama */}
      <div className="flex items-center gap-6 border-b border-slate-200">
        <button
          type="button"
          onClick={() => setPrimaryTab('list')}
          className={cn(
            'inline-flex items-center gap-2 border-b-2 px-1 pb-2.5 text-sm font-medium transition-colors',
            primaryTab === 'list'
              ? 'border-[#2563EB] text-[#2563EB]'
              : 'border-transparent text-slate-600 hover:text-slate-900',
          )}
        >
          <Users className="h-4 w-4" aria-hidden="true" />
          <span>{isEs ? 'Personal' : '员工列表'}</span>
        </button>
        <button
          type="button"
          onClick={() => setPrimaryTab('org')}
          className={cn(
            'inline-flex items-center gap-2 border-b-2 px-1 pb-2.5 text-sm font-medium transition-colors',
            primaryTab === 'org'
              ? 'border-[#2563EB] text-[#2563EB]'
              : 'border-transparent text-slate-600 hover:text-slate-900',
          )}
        >
          <Network className="h-4 w-4" aria-hidden="true" />
          <span>{isEs ? 'Organigrama' : '组织架构'}</span>
        </button>
      </div>

      {errorBanner && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
        >
          {errorBanner}
        </div>
      )}

      {/* Advanced Toolbar */}
      <div className="relative space-y-2 rounded-xl border border-slate-200/75 bg-white p-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            {primaryTab === 'list' && (
              <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
                <button
                  type="button"
                  onClick={() => setViewMode('table')}
                  aria-label={isEs ? 'Vista de tabla' : '表格视图'}
                  className={cn(
                    'rounded-md p-1.5 transition-colors',
                    viewMode === 'table'
                      ? 'bg-white text-[#2563EB] shadow-xs'
                      : 'text-slate-500 hover:text-slate-800',
                  )}
                >
                  <Table2 className="h-4 w-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={() => setViewMode('card')}
                  aria-label={isEs ? 'Vista de tarjetas' : '卡片视图'}
                  className={cn(
                    'rounded-md p-1.5 transition-colors',
                    viewMode === 'card'
                      ? 'bg-white text-[#2563EB] shadow-xs'
                      : 'text-slate-500 hover:text-slate-800',
                  )}
                >
                  <LayoutGrid className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            )}

            <div className="relative min-w-56 flex-1 sm:w-64 sm:flex-none">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-slate-400"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) {
                    e.preventDefault()
                  }
                }}
                placeholder={t('employees.searchPlaceholder')}
                className="h-8 pl-9 text-xs"
              />
            </div>

            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                setFilterPanelOpen((v) => !v)
                setColumnsPanelOpen(false)
              }}
              className={cn(
                'h-8 gap-1.5 text-xs',
                activeFiltersCount > 0 &&
                  'border-blue-200 bg-blue-50/70 text-[#2563EB]',
              )}
            >
              <Filter className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{isEs ? 'Filtrar' : '筛选'}</span>
              {activeFiltersCount > 0 && (
                <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-[#2563EB] px-1 text-[10px] font-semibold text-white tabular-nums">
                  {activeFiltersCount}
                </span>
              )}
            </Button>

            <span className="px-1 text-xs font-medium text-slate-500 tabular-nums">
              {t('employees.count', { count: filteredEmployees.length })}
            </span>
          </div>

          {primaryTab === 'list' && viewMode === 'table' && (
            <div className="relative">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => {
                  setColumnsPanelOpen((v) => !v)
                  setFilterPanelOpen(false)
                }}
                className="h-8 gap-1.5 text-xs"
              >
                <Columns3 className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{isEs ? 'Columnas' : '自定义显示列'}</span>
              </Button>

              {columnsPanelOpen && (
                <div className="absolute top-10 right-0 z-30 w-56 rounded-xl border border-slate-200 bg-white p-3 shadow-md">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-xs font-semibold text-slate-900">
                      {isEs ? 'Columnas visibles' : '显示列设置'}
                    </span>
                    <button
                      type="button"
                      onClick={() => setColumnsPanelOpen(false)}
                      className="text-slate-400 hover:text-slate-700"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                  <div className="space-y-2">
                    {[
                      {
                        id: 'employee_code',
                        label: t('employees.columns.code'),
                      },
                      {
                        id: 'job_title',
                        label: isEs ? 'Puesto / Dpto.' : '岗位 / 部门',
                      },
                      {
                        id: 'primary_store_id',
                        label: t('employees.columns.store'),
                      },
                      {
                        id: 'work_schedule_type',
                        label: t('employees.columns.schedule'),
                      },
                      {
                        id: 'nie_dni_masked',
                        label: t('employees.columns.nieMasked'),
                      },
                      {
                        id: 'employment_status',
                        label: t('employees.columns.status'),
                      },
                    ].map((col) => (
                      <label
                        key={col.id}
                        className="flex cursor-pointer items-center gap-2 text-xs text-slate-700"
                      >
                        <Checkbox
                          checked={columnVisibility[col.id] !== false}
                          onCheckedChange={(checked) =>
                            setColumnVisibility((prev) => ({
                              ...prev,
                              [col.id]: Boolean(checked),
                            }))
                          }
                        />
                        <span>{col.label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Filter Panel */}
        {filterPanelOpen && (
          <div className="grid grid-cols-1 gap-3 border-t border-slate-100 pt-3 sm:grid-cols-4">
            <div className="space-y-1">
              <Label className="text-[11px] text-slate-500">
                {t('employees.columns.store')}
              </Label>
              <Select value={storeFilter} onValueChange={setStoreFilter}>
                <SelectTrigger className="h-8 bg-white text-xs">
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
            </div>

            <div className="space-y-1">
              <Label className="text-[11px] text-slate-500">
                {t('employees.fields.department')}
              </Label>
              <Select
                value={departmentFilter}
                onValueChange={setDepartmentFilter}
              >
                <SelectTrigger className="h-8 bg-white text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {isEs ? 'Todos los departamentos' : '全部部门'}
                  </SelectItem>
                  {departments.map((d) => (
                    <SelectItem key={d} value={d}>
                      {d}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[11px] text-slate-500">
                {t('employees.columns.status')}
              </Label>
              <Select value={statusFilter} onValueChange={setStatusFilter}>
                <SelectTrigger className="h-8 bg-white text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {t('leaves.allStatuses')}
                  </SelectItem>
                  {(
                    [
                      'active',
                      'on_leave',
                      'terminated',
                      'legal_hold',
                    ] as EmploymentStatus[]
                  ).map((st) => (
                    <SelectItem key={st} value={st}>
                      {t(`employees.employmentStatuses.${st}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <Label className="text-[11px] text-slate-500">
                {t('employees.fields.contractType')}
              </Label>
              <Select value={contractFilter} onValueChange={setContractFilter}>
                <SelectTrigger className="h-8 bg-white text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {isEs ? 'Todos los contratos' : '全部合同类型'}
                  </SelectItem>
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
          </div>
        )}

        {/* Active Filter Pills */}
        {activeFiltersCount > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2">
            {storeFilter !== 'all' && (
              <Badge
                variant="outline"
                className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
              >
                <span>
                  {t('employees.columns.store')}:{' '}
                  {storeMap.get(storeFilter)?.name ?? storeFilter}
                </span>
                <button
                  type="button"
                  onClick={() => setStoreFilter('all')}
                  aria-label="Remove store filter"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </Badge>
            )}
            {departmentFilter !== 'all' && (
              <Badge
                variant="outline"
                className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
              >
                <span>
                  {t('employees.fields.department')}: {departmentFilter}
                </span>
                <button
                  type="button"
                  onClick={() => setDepartmentFilter('all')}
                  aria-label="Remove department filter"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </Badge>
            )}
            {statusFilter !== 'all' && (
              <Badge
                variant="outline"
                className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
              >
                <span>
                  {t('employees.columns.status')}:{' '}
                  {t(`employees.employmentStatuses.${statusFilter}`)}
                </span>
                <button
                  type="button"
                  onClick={() => setStatusFilter('all')}
                  aria-label="Remove status filter"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </Badge>
            )}
            {contractFilter !== 'all' && (
              <Badge
                variant="outline"
                className="gap-1 border-blue-200 bg-blue-50 text-xs text-[#2563EB]"
              >
                <span>
                  {t('employees.fields.contractType')}:{' '}
                  {t(`employees.contractTypes.${contractFilter}`)}
                </span>
                <button
                  type="button"
                  onClick={() => setContractFilter('all')}
                  aria-label="Remove contract filter"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </Badge>
            )}
            <button
              type="button"
              onClick={clearAllFilters}
              className="ml-1 text-xs font-medium text-slate-500 hover:text-slate-900"
            >
              {isEs ? 'Limpiar todo' : '清除全部'}
            </button>
          </div>
        )}
      </div>

      {/* Content Area */}
      {primaryTab === 'org' ? (
        loading ? (
          <Skeleton className="h-64 w-full rounded-xl" />
        ) : (
          <OrgChartView
            employees={filteredEmployees}
            stores={stores}
            onSelectEmployee={setSelectedEmployeeId}
          />
        )
      ) : viewMode === 'card' ? (
        loading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-32 rounded-xl" />
            ))}
          </div>
        ) : filteredEmployees.length === 0 ? (
          <EmptyState icon={Users} title={t('employees.empty')} />
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {filteredEmployees.map((emp) => {
              const st = storeMap.get(emp.primary_store_id)
              return (
                <button
                  key={emp.id}
                  type="button"
                  onClick={() => setSelectedEmployeeId(emp.id)}
                  className="flex flex-col gap-3 rounded-xl border border-slate-200/75 bg-white p-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors hover:border-blue-200 hover:bg-blue-50/20"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-3">
                      <UserAvatar
                        avatarUrl={emp.avatar_url}
                        name={emp.preferred_name || emp.full_name}
                        size="md"
                      />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-semibold text-slate-900">
                          {emp.full_name}
                        </div>
                        <div className="truncate text-xs text-slate-500">
                          {emp.job_title || emp.department || emp.employee_code}
                        </div>
                      </div>
                    </div>
                    <Badge
                      variant="outline"
                      className={cn(
                        'shrink-0 text-[10px]',
                        STATUS_BADGE_CLASS[emp.employment_status],
                      )}
                    >
                      {t(`employees.employmentStatuses.${emp.employment_status}`)}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between border-t border-slate-100 pt-2.5 text-xs text-slate-500 tabular-nums">
                    <span className="truncate">
                      {st ? `${st.code} · ${st.name}` : '—'}
                    </span>
                    <span className="shrink-0 font-medium text-slate-700">
                      {emp.contracted_weekly_hours} h
                    </span>
                  </div>
                </button>
              )
            })}
          </div>
        )
      ) : (
        <section className="overflow-hidden rounded-xl border border-slate-200/75 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader className="bg-slate-50/80">
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id} className="border-slate-200">
                    {hg.headers.map((header) => (
                      <TableHead
                        key={header.id}
                        className="h-10 px-4 text-xs font-semibold whitespace-nowrap text-slate-600"
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
                  Array.from({ length: 5 }).map((_, idx) => (
                    <TableRow key={idx} className="border-slate-200">
                      {Array.from({ length: 7 }).map((__, colIdx) => (
                        <TableCell key={colIdx} className="px-4 py-3">
                          <Skeleton className="h-4 w-24" />
                        </TableCell>
                      ))}
                    </TableRow>
                  ))
                ) : table.getRowModel().rows.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="py-12 text-center text-sm text-slate-500"
                    >
                      {t('employees.empty')}
                    </TableCell>
                  </TableRow>
                ) : (
                  table.getRowModel().rows.map((row) => (
                    <TableRow
                      key={row.id}
                      onClick={() => setSelectedEmployeeId(row.original.id)}
                      className="cursor-pointer border-slate-200 hover:bg-blue-50/30"
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
      )}

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
                className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
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
                <Label htmlFor="emp-job-title">
                  {isEs ? 'Puesto' : '职位'}
                </Label>
                <Input
                  id="emp-job-title"
                  value={form.job_title}
                  onChange={(e) =>
                    setForm({ ...form, job_title: e.target.value })
                  }
                  placeholder={isEs ? 'Camarero / Chef' : '店长 / 厨师'}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="emp-department">
                  {t('employees.fields.department')}
                </Label>
                <Input
                  id="emp-department"
                  value={form.department}
                  onChange={(e) =>
                    setForm({ ...form, department: e.target.value })
                  }
                  placeholder="Cocina / Sala"
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

            <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3.5">
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
              className="bg-[#2563EB] text-white hover:bg-blue-700"
            >
              {saving
                ? t('employees.drawer.saving')
                : t('employees.drawer.save')}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>

      <BulkImportSheet
        open={bulkImportOpen}
        onOpenChange={setBulkImportOpen}
        onImported={loadData}
      />
    </div>
  )
}
