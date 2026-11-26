import { useMemo } from 'react'
import { Building2, Store as StoreIcon, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type {
  EmployeeResponse,
  EmploymentStatus,
  StoreResponse,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
import { Badge } from '@/components/ui/badge'
import { useAuth } from '@/features/auth/AuthContext'
import { cn } from '@/lib/utils'

interface OrgChartViewProps {
  employees: EmployeeResponse[]
  stores: StoreResponse[]
  onSelectEmployee: (employeeId: string) => void
}

const STATUS_PILL: Record<EmploymentStatus, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  on_leave: 'border-amber-200 bg-amber-50 text-amber-800',
  terminated: 'border-slate-200 bg-slate-100 text-slate-600',
  legal_hold: 'border-rose-200 bg-rose-50 text-rose-700',
  anonymized: 'border-slate-200 bg-slate-100 text-slate-500',
}

export function OrgChartView({
  employees,
  stores,
  onSelectEmployee,
}: OrgChartViewProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')

  const tree = useMemo(() => {
    const storeMap = new Map<string, StoreResponse>()
    for (const s of stores) {
      storeMap.set(s.id, s)
    }

    const byStore = new Map<
      string,
      {
        storeId: string
        storeLabel: string
        departments: Map<string, EmployeeResponse[]>
      }
    >()

    for (const emp of employees) {
      const st = storeMap.get(emp.primary_store_id)
      const storeLabel = st
        ? `${st.code} · ${st.name}`
        : isEs
          ? 'Centro sin asignar'
          : '未分配门店'
      let storeGroup = byStore.get(emp.primary_store_id)
      if (!storeGroup) {
        storeGroup = {
          storeId: emp.primary_store_id,
          storeLabel,
          departments: new Map(),
        }
        byStore.set(emp.primary_store_id, storeGroup)
      }
      const deptKey = emp.department?.trim() || (isEs ? 'General' : '综合部门')
      const list = storeGroup.departments.get(deptKey) ?? []
      list.push(emp)
      storeGroup.departments.set(deptKey, list)
    }

    return Array.from(byStore.values())
  }, [employees, stores, isEs])

  if (employees.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title={t('employees.empty')}
        description={
          isEs
            ? 'No hay empleados que coincidan con los filtros actuales.'
            : '当前筛选条件下暂无员工数据。'
        }
      />
    )
  }

  return (
    <div className="space-y-6">
      {/* Root Company Entity Node */}
      <div className="flex justify-center">
        <div className="inline-flex items-center gap-3 rounded-xl border border-blue-200 bg-white px-5 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#2563EB] text-white">
            <Building2 className="h-4 w-4" aria-hidden="true" />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-900">
              {activeTenant?.tenant_trade_name ?? t('brand')}
            </div>
            <div className="text-xs text-slate-500 tabular-nums">
              {activeTenant?.tenant_legal_name} · {employees.length}{' '}
              {isEs ? 'personas' : '人'}
            </div>
          </div>
        </div>
      </div>

      {/* Store Branches */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {tree.map((storeNode) => {
          const totalInStore = Array.from(
            storeNode.departments.values(),
          ).reduce((acc, arr) => acc + arr.length, 0)

          return (
            <div
              key={storeNode.storeId}
              className="rounded-xl border border-slate-200/75 bg-white p-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
            >
              <div className="mb-4 flex items-center justify-between gap-2 border-b border-slate-100 pb-3">
                <div className="flex items-center gap-2.5">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-100 text-[#2563EB]">
                    <StoreIcon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <div className="text-sm font-semibold text-slate-900">
                      {storeNode.storeLabel}
                    </div>
                    <div className="text-[11px] text-slate-500 tabular-nums">
                      {storeNode.departments.size}{' '}
                      {isEs ? 'departamentos' : '个部门'} · {totalInStore}{' '}
                      {isEs ? 'personas' : '人'}
                    </div>
                  </div>
                </div>
              </div>

              <div className="space-y-4">
                {Array.from(storeNode.departments.entries()).map(
                  ([deptName, members]) => (
                    <div key={deptName} className="space-y-2">
                      <div className="flex items-center justify-between px-1">
                        <span className="text-xs font-semibold tracking-wide text-slate-600 uppercase">
                          {deptName}
                        </span>
                        <span className="text-[11px] text-slate-400 tabular-nums">
                          {members.length}
                        </span>
                      </div>

                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {members.map((emp) => (
                          <button
                            key={emp.id}
                            type="button"
                            onClick={() => onSelectEmployee(emp.id)}
                            className="flex items-center justify-between gap-2.5 rounded-lg border border-slate-200/80 bg-slate-50/50 p-2.5 text-left transition-colors hover:border-blue-200 hover:bg-blue-50/40 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                          >
                            <div className="flex min-w-0 items-center gap-2.5">
                              <UserAvatar
                                avatarUrl={emp.avatar_url}
                                name={emp.preferred_name || emp.full_name}
                                size="sm"
                              />
                              <div className="min-w-0">
                                <div className="truncate text-xs font-semibold text-slate-900">
                                  {emp.full_name}
                                </div>
                                <div className="truncate text-[11px] text-slate-500">
                                  {emp.job_title || emp.employee_code}
                                </div>
                              </div>
                            </div>
                            <Badge
                              variant="outline"
                              className={cn(
                                'shrink-0 text-[10px]',
                                STATUS_PILL[emp.employment_status],
                              )}
                            >
                              {t(
                                `employees.employmentStatuses.${emp.employment_status}`,
                              )}
                            </Badge>
                          </button>
                        ))}
                      </div>
                    </div>
                  ),
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
