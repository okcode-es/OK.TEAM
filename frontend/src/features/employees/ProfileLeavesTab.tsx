import { useCallback, useEffect, useState } from 'react'
import { CalendarClock, CalendarCog, CalendarPlus, FileText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  EmployeeResponse,
  LeaveRequestResponse,
  LeaveStatus,
  VacationBalanceResponse,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { AssignLeaveSheet } from '@/features/leaves/AssignLeaveSheet'
import { LeaveEntitlementDialog } from '@/features/leaves/LeaveEntitlementDialog'

interface ProfileLeavesTabProps {
  employee: EmployeeResponse
}

const LEAVE_STATUS_PILL: Record<LeaveStatus, string> = {
  pending_manager: 'border-amber-200 bg-amber-50 text-amber-800',
  pending_hr: 'border-sky-200 bg-sky-50 text-sky-700',
  approved: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  rejected: 'border-rose-200 bg-rose-50 text-rose-700',
  cancelled: 'border-slate-200 bg-slate-100 text-slate-600',
}

export function ProfileLeavesTab({ employee }: ProfileLeavesTabProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const isEs = (i18n.language || 'zh-CN').startsWith('es')
  const role = activeTenant?.role
  const canManageLeaves =
    role === 'owner' || role === 'hr' || role === 'store_manager'

  const [balance, setBalance] = useState<VacationBalanceResponse | null>(null)
  const [leaves, setLeaves] = useState<LeaveRequestResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [entitlementOpen, setEntitlementOpen] = useState(false)
  const [assignOpen, setAssignOpen] = useState(false)

  const loadLeaves = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const year = new Date().getFullYear()
      const [balRes, listRes] = await Promise.all([
        apiClient.GET('/api/v1/leaves/balance', {
          params: { query: { employee_id: employee.id, year } },
        }),
        apiClient.GET('/api/v1/leaves', {
          params: { query: { employee_id: employee.id, year } },
        }),
      ])
      if (listRes.error) {
        setError(extractApiError(listRes.error, t('common.error')))
        return
      }
      if (balRes.data) setBalance(balRes.data)
      if (listRes.data) setLeaves(listRes.data.items)
    } finally {
      setLoading(false)
    }
  }, [employee.id, t])

  useEffect(() => {
    void loadLeaves()
  }, [loadLeaves])

  const handleOpenJustificante = async (leaveId: string) => {
    const res = await apiClient.POST(
      '/api/v1/leaves/{leave_id}/justificante/signed-url',
      {
        params: { path: { leave_id: leaveId } },
      },
    )
    if (res.data) {
      window.open(res.data.url, '_blank', 'noopener,noreferrer')
    }
  }

  return (
    <div className="space-y-5">
      {canManageLeaves && (
        <div className="flex flex-wrap items-center justify-end gap-2">
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
            onClick={() => setAssignOpen(true)}
            className="bg-[#2563EB] text-white hover:bg-blue-700"
          >
            <CalendarPlus className="h-4 w-4" aria-hidden="true" />
            {isEs ? 'Asignar ausencia' : '为员工安排假期'}
          </Button>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
        >
          {error}
        </div>
      )}

      {/* Vacation Balance KPIs */}
      {balance && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Card className="gap-1 py-4">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-xs font-medium text-slate-500">
                {t('leaves.balance.entitled')}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 text-2xl font-bold text-slate-900 tabular-nums">
              {balance.entitled_days} d
            </CardContent>
          </Card>
          <Card className="gap-1 py-4">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-xs font-medium text-slate-500">
                {t('leaves.balance.used')}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 text-2xl font-bold text-slate-900 tabular-nums">
              {balance.approved_days} d
            </CardContent>
          </Card>
          <Card className="gap-1 py-4">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-xs font-medium text-slate-500">
                {t('leaves.balance.pending')}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 text-2xl font-bold text-amber-700 tabular-nums">
              {balance.pending_days} d
            </CardContent>
          </Card>
          <Card className="gap-1 border-blue-200 bg-blue-50/50 py-4">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-xs font-medium text-[#2563EB]">
                {t('leaves.balance.remaining')}
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 text-2xl font-bold text-[#2563EB] tabular-nums">
              {balance.remaining_days} d
            </CardContent>
          </Card>
        </div>
      )}

      {/* Leave History */}
      {loading ? (
        <Skeleton className="h-44 w-full rounded-xl" />
      ) : leaves.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title={isEs ? 'Sin solicitudes de ausencia' : '本年度暂无请假记录'}
        />
      ) : (
        <Card className="gap-3 py-5">
          <CardHeader className="px-5 pb-0">
            <CardTitle className="text-sm font-semibold text-slate-900">
              {isEs ? 'Historial de ausencias' : '请假与休假明细'}
            </CardTitle>
          </CardHeader>
          <CardContent className="px-5">
            <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
              {leaves.map((lv) => (
                <li
                  key={lv.id}
                  className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-slate-900">
                        {t(`leaves.types.${lv.leave_type}`)}
                      </span>
                      <Badge
                        variant="outline"
                        className={LEAVE_STATUS_PILL[lv.status]}
                      >
                        {t(`leaves.statuses.${lv.status}`)}
                      </Badge>
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500 tabular-nums">
                      {lv.start_date} → {lv.end_date} · {lv.natural_days}{' '}
                      {isEs ? 'días naturales' : '自然日'}
                    </div>
                  </div>
                  {role !== 'accountant' && lv.medical_document_id && (
                    <Button
                      type="button"
                      size="xs"
                      variant="outline"
                      onClick={() => void handleOpenJustificante(lv.id)}
                    >
                      <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                      Justificante
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <LeaveEntitlementDialog
        open={entitlementOpen}
        onOpenChange={setEntitlementOpen}
        lockedEmployee={employee}
        onSuccess={loadLeaves}
      />

      <AssignLeaveSheet
        open={assignOpen}
        onOpenChange={setAssignOpen}
        lockedEmployee={employee}
        onSuccess={loadLeaves}
      />
    </div>
  )
}
