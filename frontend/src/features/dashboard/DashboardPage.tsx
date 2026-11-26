import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Award,
  Bell,
  Cake,
  Calendar,
  CalendarClock,
  Check,
  CheckCircle2,
  Clock,
  Download,
  FileCheck2,
  FileText,
  Megaphone,
  Paperclip,
  Pin,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  AdminDashboardResponse,
  AnnouncementResponse,
  DocumentListItemResponse,
  EmployeeResponse,
  EmploymentStatus,
  StoreResponse,
  UpcomingAnniversaryResponse,
  UpcomingBirthdayResponse,
  WhoIsInGroupResponse,
  WhoIsInStatus,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
import type { NavSection } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/features/auth/AuthContext'
import { useClockState } from '@/features/clock/ClockStateContext'
import { WorkRingClockCard } from '@/features/dashboard/WorkRingClockCard'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

interface DashboardPageProps {
  onNavigate: (section: NavSection) => void
  onOpenEmployeeProfile?: (employeeId: string) => void
}

const EMPLOYEE_STATUS_PILL: Record<EmploymentStatus, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  on_break: 'border-amber-200 bg-amber-50 text-amber-800',
  on_leave: 'border-amber-200 bg-amber-50 text-amber-800',
  terminated: 'border-slate-200 bg-slate-100 text-slate-600',
  legal_hold: 'border-rose-200 bg-rose-50 text-rose-700',
  anonymized: 'border-slate-200 bg-slate-100 text-slate-500',
} as Record<EmploymentStatus, string>

const WHO_IS_IN_ORDER: WhoIsInStatus[] = [
  'working',
  'remote',
  'on_break',
  'vacation',
  'absence',
  'absenteeism',
]

const WHO_IS_IN_BADGE: Record<WhoIsInStatus, string> = {
  working: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  remote: 'border-sky-200 bg-sky-50 text-sky-700',
  on_break: 'border-amber-200 bg-amber-50 text-amber-800',
  vacation: 'border-sky-200 bg-sky-50 text-sky-700',
  absence: 'border-rose-200 bg-rose-50 text-rose-700',
  absenteeism: 'border-rose-200 bg-rose-50 text-rose-800',
}

function getGreeting(isEs: boolean, name: string): string {
  const hour = new Date().getHours()
  if (isEs) {
    if (hour < 12) return `Buenos días, ${name}`
    if (hour < 20) return `Buenas tardes, ${name}`
    return `Buenas noches, ${name}`
  }
  if (hour < 12) return `早上好，${name}`
  if (hour < 18) return `下午好，${name}`
  return `晚上好，${name}`
}

type CelebrationItem =
  | {
      kind: 'birthday'
      key: string
      employee_id: string
      full_name: string
      preferred_name: string
      store_name: string
      avatar_url: string | null
      days_until: number
      month: number
      day: number
      birthday: UpcomingBirthdayResponse
    }
  | {
      kind: 'anniversary'
      key: string
      employee_id: string
      full_name: string
      preferred_name: string
      store_name: string
      avatar_url: string | null
      days_until: number
      month: number
      day: number
      years: number
      anniversary: UpcomingAnniversaryResponse
    }

export function DashboardPage({
  onNavigate,
  onOpenEmployeeProfile,
}: DashboardPageProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const { myDashboard, loading: myLoading, refreshClockState } = useClockState()

  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const role = activeTenant?.role
  const isEmployeeRole = role === 'employee'

  const [viewMode, setViewMode] = useState<'admin' | 'employee'>(() =>
    isEmployeeRole ? 'employee' : 'admin',
  )

  useEffect(() => {
    setViewMode(isEmployeeRole ? 'employee' : 'admin')
  }, [isEmployeeRole, activeTenant?.tenant_id])

  // Admin state
  const [stores, setStores] = useState<StoreResponse[]>([])
  const [employees, setEmployees] = useState<EmployeeResponse[]>([])
  const [empSearchQuery, setEmpSearchQuery] = useState('')
  const [empSearchOpen, setEmpSearchOpen] = useState(false)
  const empSearchRef = useRef<HTMLDivElement | null>(null)
  const [storeFilter, setStoreFilter] = useState<string>('all')
  const [departmentFilter, setDepartmentFilter] = useState<string>('all')
  const [adminData, setAdminData] = useState<AdminDashboardResponse | null>(
    null,
  )
  const [adminLoading, setAdminLoading] = useState(false)
  const [adminError, setAdminError] = useState<string | null>(null)
  const [announcementTab, setAnnouncementTab] = useState<
    'all' | 'notice' | 'policy'
  >('all')

  // Celebrations congratulate state
  const [congratulated, setCongratulated] = useState<Record<string, boolean>>(
    {},
  )

  // Drawers
  const [activeWhoGroup, setActiveWhoGroup] =
    useState<WhoIsInGroupResponse | null>(null)
  const [selectedAnnouncement, setSelectedAnnouncement] =
    useState<AnnouncementResponse | null>(null)
  const [annLoading, setAnnLoading] = useState(false)
  const [ackBusyId, setAckBusyId] = useState<string | null>(null)
  const [docBusyId, setDocBusyId] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)

  const loadStores = useCallback(async () => {
    try {
      const res = await apiClient.GET('/api/v1/stores')
      if (res.data) {
        setStores(res.data.items)
      }
    } catch {
      // ignore non-critical store metadata failure
    }
  }, [])

  const loadScopedEmployees = useCallback(async () => {
    if (isEmployeeRole) return
    try {
      const res = await apiClient.GET('/api/v1/employees')
      if (res.error || !res.data) {
        setAdminError((prev) => prev ?? extractApiError(res.error, t('common.error')))
        return
      }
      setEmployees(res.data.items)
    } catch (err) {
      setAdminError((prev) => prev ?? extractApiError(err, t('common.error')))
    }
  }, [isEmployeeRole, t])

  const loadAdminDashboard = useCallback(async () => {
    if (isEmployeeRole) return
    setAdminLoading(true)
    setAdminError(null)
    try {
      const res = await apiClient.GET('/api/v1/dashboard/admin', {
        params: {
          query: {
            store_id: storeFilter !== 'all' ? storeFilter : undefined,
            department:
              departmentFilter !== 'all' ? departmentFilter : undefined,
          },
        },
      })
      if (res.error || !res.data) {
        setAdminError(extractApiError(res.error, t('common.error')))
        return
      }
      setAdminData(res.data)
    } finally {
      setAdminLoading(false)
    }
  }, [isEmployeeRole, storeFilter, departmentFilter, t])

  useEffect(() => {
    if (!isEmployeeRole) {
      void loadStores()
      void loadScopedEmployees()
    }
  }, [isEmployeeRole, loadStores, loadScopedEmployees, activeTenant?.tenant_id])

  useEffect(() => {
    if (!empSearchOpen) return
    const handleOutsideClick = (event: MouseEvent) => {
      if (
        empSearchRef.current &&
        !empSearchRef.current.contains(event.target as Node)
      ) {
        setEmpSearchOpen(false)
      }
    }
    document.addEventListener('mousedown', handleOutsideClick)
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [empSearchOpen])

  useEffect(() => {
    void loadAdminDashboard()
  }, [loadAdminDashboard, activeTenant?.tenant_id])

  const openAnnouncementDetail = async (announcementId: string) => {
    setAnnLoading(true)
    setActionNotice(null)
    try {
      const res = await apiClient.GET(
        '/api/v1/announcements/{announcement_id}',
        {
          params: { path: { announcement_id: announcementId } },
        },
      )
      if (res.data) {
        setSelectedAnnouncement(res.data)
      }
    } finally {
      setAnnLoading(false)
    }
  }

  const handleAcknowledgeAnnouncement = async (announcementId: string) => {
    setAckBusyId(announcementId)
    setActionNotice(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/announcements/{announcement_id}/acknowledge',
        {
          params: { path: { announcement_id: announcementId } },
        },
      )
      if (res.error) {
        setActionNotice(extractApiError(res.error, t('common.error')))
        return
      }
      setActionNotice(t('announcements.acknowledgedSuccess'))
      if (selectedAnnouncement?.id === announcementId) {
        await openAnnouncementDetail(announcementId)
      }
      await Promise.all([refreshClockState(), loadAdminDashboard()])
    } finally {
      setAckBusyId(null)
    }
  }

  const handleAnnouncementAttachment = async (announcementId: string) => {
    setAnnLoading(true)
    try {
      const res = await apiClient.POST(
        '/api/v1/announcements/{announcement_id}/attachment/signed-url',
        {
          params: { path: { announcement_id: announcementId } },
        },
      )
      if (res.data) {
        window.open(res.data.url, '_blank', 'noopener,noreferrer')
      }
    } finally {
      setAnnLoading(false)
    }
  }

  const handleDownloadDocument = async (docId: string) => {
    setDocBusyId(docId)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/signed-url',
        {
          params: { path: { document_id: docId } },
          body: { ttl_seconds: 60 },
        },
      )
      if (res.data) {
        window.open(res.data.url, '_blank', 'noopener,noreferrer')
      }
    } finally {
      setDocBusyId(null)
    }
  }

  const handleSignDocument = async (docId: string) => {
    setDocBusyId(docId)
    setActionNotice(null)
    try {
      const res = await apiClient.POST(
        '/api/v1/documents/{document_id}/receipts',
        {
          params: { path: { document_id: docId } },
        },
      )
      if (res.error) {
        setActionNotice(extractApiError(res.error, t('common.error')))
        return
      }
      setActionNotice(t('documents.detailDrawer.signedSuccess'))
      await refreshClockState()
    } finally {
      setDocBusyId(null)
    }
  }

  const storeMap = useMemo(() => {
    const map = new Map<string, StoreResponse>()
    for (const s of stores) map.set(s.id, s)
    return map
  }, [stores])

  const matchedEmployees = useMemo(() => {
    const q = empSearchQuery.trim().toLowerCase()
    if (!q) return []
    return employees
      .filter((emp) => {
        const st = storeMap.get(emp.primary_store_id)
        const haystack = [
          emp.full_name,
          emp.preferred_name,
          emp.employee_code,
          emp.job_title,
          emp.department,
          emp.email,
          st?.name,
          st?.code,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
        return haystack.includes(q)
      })
      .slice(0, 8)
  }, [employees, empSearchQuery, storeMap])

  const filteredAdminAnnouncements = useMemo(() => {
    const list = adminData?.recent_announcements ?? []
    if (announcementTab === 'all') return list
    return list.filter((a) => a.category === announcementTab)
  }, [adminData?.recent_announcements, announcementTab])

  const whoIsInMap = useMemo(() => {
    const map = new Map<WhoIsInStatus, WhoIsInGroupResponse>()
    for (const g of adminData?.who_is_in ?? []) {
      map.set(g.status, g)
    }
    return map
  }, [adminData?.who_is_in])

  const todayFormatted = useMemo(() => {
    return new Intl.DateTimeFormat(locale, {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      year: 'numeric',
    }).format(new Date())
  }, [locale])

  const userDisplayName =
    myDashboard?.employee?.preferred_name ||
    myDashboard?.employee?.full_name ||
    activeTenant?.display_name ||
    ''

  const celebrations = useMemo<CelebrationItem[]>(() => {
    const sourceBirthdays =
      viewMode === 'admin'
        ? (adminData?.upcoming_birthdays ?? [])
        : (myDashboard?.upcoming_birthdays ?? [])
    const sourceAnniversaries =
      viewMode === 'admin'
        ? (adminData?.upcoming_anniversaries ?? [])
        : (myDashboard?.upcoming_anniversaries ?? [])

    const items: CelebrationItem[] = [
      ...sourceBirthdays.map((b) => ({
        kind: 'birthday' as const,
        key: `b-${b.employee_id}`,
        employee_id: b.employee_id,
        full_name: b.full_name,
        preferred_name: b.preferred_name,
        store_name: b.store_name,
        avatar_url: b.avatar_url,
        days_until: b.days_until,
        month: b.birth_month,
        day: b.birth_day,
        birthday: b,
      })),
      ...sourceAnniversaries.map((a) => ({
        kind: 'anniversary' as const,
        key: `a-${a.employee_id}`,
        employee_id: a.employee_id,
        full_name: a.full_name,
        preferred_name: a.preferred_name,
        store_name: a.store_name,
        avatar_url: a.avatar_url,
        days_until: a.days_until,
        month: a.anniversary_month,
        day: a.anniversary_day,
        years: a.years_of_service,
        anniversary: a,
      })),
    ]
    return items.sort((x, y) => x.days_until - y.days_until)
  }, [viewMode, adminData, myDashboard])

  const renderCelebrationList = (limit = 6) => {
    if (celebrations.length === 0) {
      return (
        <p className="text-xs text-slate-500">
          {isEs
            ? 'No hay cumpleaños ni aniversarios próximos.'
            : '近期暂无生日或入职周年纪念。'}
        </p>
      )
    }
    return (
      <ul className="space-y-2.5">
        {celebrations.slice(0, limit).map((item) => {
          const isSent = Boolean(congratulated[item.key])
          return (
            <li
              key={item.key}
              className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/50 px-3 py-2"
            >
              <div className="flex min-w-0 items-center gap-3">
                <div className="relative shrink-0">
                  <UserAvatar
                    avatarUrl={item.avatar_url}
                    name={item.preferred_name || item.full_name}
                    size="sm"
                  />
                  <span
                    className={cn(
                      'absolute -right-1 -bottom-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full border border-white px-1 text-[9px] font-bold tabular-nums',
                      item.kind === 'anniversary'
                        ? 'bg-[#2563EB] text-white'
                        : 'bg-amber-500 text-white',
                    )}
                  >
                    {item.kind === 'anniversary' ? (
                      isEs ? (
                        `${item.years}a`
                      ) : (
                        `${item.years}年`
                      )
                    ) : (
                      <Cake className="h-2.5 w-2.5" aria-hidden="true" />
                    )}
                  </span>
                </div>
                <div className="min-w-0">
                  {viewMode === 'admin' && !isEmployeeRole && onOpenEmployeeProfile ? (
                    <button
                      type="button"
                      onClick={() => onOpenEmployeeProfile(item.employee_id)}
                      className="truncate text-left text-xs font-semibold text-slate-900 hover:text-[#2563EB] hover:underline"
                    >
                      {item.full_name}
                    </button>
                  ) : (
                    <div className="truncate text-xs font-semibold text-slate-900">
                      {item.full_name}
                    </div>
                  )}
                  <div className="truncate text-[11px] text-slate-500">
                    {item.kind === 'anniversary'
                      ? isEs
                        ? `Aniversario laboral · ${item.years} años`
                        : `入职 ${item.years} 周年`
                      : isEs
                        ? 'Cumpleaños'
                        : '生日快乐'}{' '}
                    ·{' '}
                    <span className="tabular-nums">
                      {String(item.month).padStart(2, '0')}-
                      {String(item.day).padStart(2, '0')}
                    </span>
                  </div>
                </div>
              </div>

              <Button
                type="button"
                size="xs"
                variant={isSent ? 'outline' : 'secondary'}
                onClick={() =>
                  setCongratulated((prev) => ({
                    ...prev,
                    [item.key]: !prev[item.key],
                  }))
                }
                className={cn(
                  'shrink-0 text-[11px]',
                  isSent &&
                    'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100',
                )}
              >
                {isSent ? (
                  <>
                    <Check className="h-3 w-3" aria-hidden="true" />
                    {isEs ? 'Enviado' : '已祝贺'}
                  </>
                ) : (
                  <>
                    <Sparkles className="h-3 w-3" aria-hidden="true" />
                    {isEs ? 'Felicitar' : '祝贺'}
                  </>
                )}
              </Button>
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <div className="space-y-5">
      {/* Top Greeting Header + Admin/Employee View Switcher */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-0.5">
          <div className="text-xs font-medium text-slate-500 capitalize tabular-nums">
            {todayFormatted}
          </div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">
            {getGreeting(isEs, userDisplayName)}
          </h1>
        </div>

        {!isEmployeeRole && (
          <div
            className="inline-flex self-start rounded-lg border border-slate-200 bg-white p-1 shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
            role="group"
            aria-label={t('dashboard.viewSwitchLabel')}
          >
            <button
              type="button"
              onClick={() => setViewMode('admin')}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                viewMode === 'admin'
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {t('dashboard.views.admin')}
            </button>
            <button
              type="button"
              onClick={() => setViewMode('employee')}
              className={cn(
                'rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                viewMode === 'employee'
                  ? 'bg-[#2563EB] text-white'
                  : 'text-slate-600 hover:text-slate-900',
              )}
            >
              {t('dashboard.views.employee')}
            </button>
          </div>
        )}
      </div>

      {actionNotice && (
        <div
          role="status"
          className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700"
        >
          {actionNotice}
        </div>
      )}

      {/* ADMIN VIEW */}
      {viewMode === 'admin' && !isEmployeeRole && (
        <div className="space-y-4">
          {/* 4 Top Compact KPI Alert Cards */}
          <div className="grid grid-cols-2 gap-3.5 lg:grid-cols-4">
            {[
              {
                key: 'pending_leaves',
                label: isEs ? 'Solicitudes pendientes' : '待审批请假',
                count: adminData?.pending_leaves_count ?? 0,
                icon: CalendarClock,
                tone: 'amber' as const,
                onClick: () => onNavigate('leaves'),
                disabled: false,
              },
              {
                key: 'attendance_alerts',
                label: isEs ? 'Alertas de jornada' : '考勤异常 / 法规预警',
                count: adminData?.attendance_alerts_count ?? 0,
                icon: AlertTriangle,
                tone: 'rose' as const,
                onClick: () => onNavigate('attendance'),
                disabled: false,
              },
              {
                key: 'pending_justificantes',
                label: isEs ? 'Justificantes pendientes' : '待核验病假证明',
                count:
                  role === 'accountant'
                    ? 0
                    : (adminData?.pending_justificantes_count ?? 0),
                icon: FileCheck2,
                tone: 'amber' as const,
                onClick: () => onNavigate('leaves'),
                disabled: role === 'accountant',
              },
              {
                key: 'unack_announcements',
                label: isEs ? 'Lecturas pendientes' : '公告未签收人数',
                count: adminData?.unacknowledged_announcements_count ?? 0,
                icon: Bell,
                tone: 'amber' as const,
                onClick: () => onNavigate('announcements'),
                disabled: false,
              },
            ].map((kpi) => {
              const Icon = kpi.icon
              const hasAlert = kpi.count > 0 && !kpi.disabled
              const highlightCls = hasAlert
                ? kpi.tone === 'rose'
                  ? 'border-rose-200 bg-rose-50/35 hover:border-rose-300'
                  : 'border-amber-200 bg-amber-50/35 hover:border-amber-300'
                : 'border-slate-200/75 bg-white hover:border-slate-300'
              const badgeCls = hasAlert
                ? kpi.tone === 'rose'
                  ? 'border-rose-200 bg-rose-50 text-rose-700'
                  : 'border-amber-200 bg-amber-50 text-amber-800'
                : 'border-slate-200 bg-slate-100 text-slate-600'

              return (
                <button
                  key={kpi.key}
                  type="button"
                  disabled={kpi.disabled}
                  onClick={kpi.onClick}
                  className={cn(
                    'flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none disabled:cursor-default disabled:opacity-80',
                    highlightCls,
                  )}
                >
                  <div className="min-w-0 space-y-1">
                    <div className="truncate text-xs font-medium text-slate-600">
                      {kpi.label}
                    </div>
                    <div
                      className={cn(
                        'text-2xl font-bold tracking-tight tabular-nums',
                        hasAlert
                          ? kpi.tone === 'rose'
                            ? 'text-rose-700'
                            : 'text-amber-800'
                          : 'text-slate-700',
                      )}
                    >
                      {kpi.count}
                    </div>
                  </div>
                  <span
                    className={cn(
                      'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border',
                      badgeCls,
                    )}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                </button>
              )
            })}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2.5 rounded-xl border border-slate-200/75 bg-white p-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
            <div className="flex flex-1 flex-wrap items-center gap-2">
              {/* Direct Employee Search */}
              <div
                ref={empSearchRef}
                className="relative min-w-64 flex-1 sm:max-w-sm"
              >
                <Search
                  className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-slate-400"
                  aria-hidden="true"
                />
                <Input
                  type="text"
                  value={empSearchQuery}
                  onFocus={() => {
                    if (empSearchQuery.trim()) setEmpSearchOpen(true)
                  }}
                  onChange={(e) => {
                    const val = e.target.value
                    setEmpSearchQuery(val)
                    setEmpSearchOpen(Boolean(val.trim()))
                  }}
                  onKeyDown={(e) => {
                    if (isImeComposing(e)) return
                    if (e.key === 'Escape') {
                      setEmpSearchOpen(false)
                    } else if (
                      e.key === 'Enter' &&
                      matchedEmployees.length > 0 &&
                      onOpenEmployeeProfile
                    ) {
                      e.preventDefault()
                      setEmpSearchOpen(false)
                      onOpenEmployeeProfile(matchedEmployees[0].id)
                    }
                  }}
                  placeholder={t('dashboard.employeeSearch.placeholder')}
                  aria-label={t('dashboard.employeeSearch.placeholder')}
                  className="h-8 pr-7 pl-8 text-xs"
                />
                {empSearchQuery && (
                  <button
                    type="button"
                    onClick={() => {
                      setEmpSearchQuery('')
                      setEmpSearchOpen(false)
                    }}
                    aria-label={t('dashboard.employeeSearch.clear')}
                    className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                )}

                {empSearchOpen && empSearchQuery.trim() && (
                  <div className="absolute top-full left-0 z-30 mt-1.5 w-full min-w-80 rounded-xl border border-slate-200 bg-white p-1.5 shadow-md">
                    {matchedEmployees.length === 0 ? (
                      <div className="px-3 py-4 text-center text-xs text-slate-500">
                        {t('dashboard.employeeSearch.noResults')}
                      </div>
                    ) : (
                      <ul className="max-h-80 space-y-0.5 overflow-y-auto">
                        {matchedEmployees.map((emp) => {
                          const st = storeMap.get(emp.primary_store_id)
                          return (
                            <li key={emp.id}>
                              <button
                                type="button"
                                onClick={() => {
                                  setEmpSearchOpen(false)
                                  onOpenEmployeeProfile?.(emp.id)
                                }}
                                className="flex w-full items-center justify-between gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                              >
                                <div className="flex min-w-0 items-center gap-2.5">
                                  <UserAvatar
                                    avatarUrl={emp.avatar_url}
                                    name={emp.preferred_name || emp.full_name}
                                    size="sm"
                                  />
                                  <div className="min-w-0">
                                    <div className="flex items-center gap-1.5">
                                      <span className="truncate text-xs font-semibold text-slate-900">
                                        {emp.full_name}
                                      </span>
                                      <span className="shrink-0 font-mono text-[11px] text-slate-500 tabular-nums">
                                        {emp.employee_code}
                                      </span>
                                    </div>
                                    <div className="truncate text-[11px] text-slate-500">
                                      {[
                                        emp.job_title || emp.department,
                                        st ? st.name : null,
                                      ]
                                        .filter(Boolean)
                                        .join(' · ') || emp.email}
                                    </div>
                                  </div>
                                </div>
                                <Badge
                                  variant="outline"
                                  className={cn(
                                    'shrink-0 text-[10px]',
                                    EMPLOYEE_STATUS_PILL[
                                      emp.employment_status
                                    ] ?? 'border-slate-200 bg-slate-100 text-slate-600',
                                  )}
                                >
                                  {t(
                                    `employees.employmentStatuses.${emp.employment_status}`,
                                  )}
                                </Badge>
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    )}
                  </div>
                )}
              </div>

              <Select value={storeFilter} onValueChange={setStoreFilter}>
                <SelectTrigger className="h-8 min-w-44 bg-white text-xs">
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

              <Select
                value={departmentFilter}
                onValueChange={setDepartmentFilter}
              >
                <SelectTrigger className="h-8 min-w-44 bg-white text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">
                    {isEs ? 'Todos los departamentos' : '全部部门'}
                  </SelectItem>
                  {(adminData?.available_departments ?? []).map((dept) => (
                    <SelectItem key={dept} value={dept}>
                      {dept}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {adminError && (
            <div
              role="alert"
              className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
            >
              {adminError}
            </div>
          )}

          {adminLoading && !adminData ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12">
              <Skeleton className="h-72 rounded-xl xl:col-span-4" />
              <Skeleton className="h-72 rounded-xl xl:col-span-4" />
              <Skeleton className="h-72 rounded-xl xl:col-span-4" />
            </div>
          ) : (
            adminData && (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12">
                {/* Who's in */}
                <Card className="xl:col-span-4">
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <Users className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                      {t('dashboard.whoIsIn.title')}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="px-5 pt-0 pb-3">
                    <div className="divide-y divide-slate-100">
                      {WHO_IS_IN_ORDER.map((statusKey) => {
                        const group = whoIsInMap.get(statusKey) ?? {
                          status: statusKey,
                          count: 0,
                          preview_members: [],
                          overflow_count: 0,
                          members: [],
                        }
                        return (
                          <button
                            key={statusKey}
                            type="button"
                            onClick={() => setActiveWhoGroup(group)}
                            className="flex w-full items-center justify-between gap-3 py-2.5 text-left transition-colors hover:bg-slate-50/80 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                          >
                            <Badge
                              variant="outline"
                              className={cn(
                                'shrink-0 text-[11px]',
                                WHO_IS_IN_BADGE[statusKey],
                              )}
                            >
                              {t(`dashboard.whoIsIn.statuses.${statusKey}`)}
                            </Badge>

                            <div className="flex min-w-0 items-center gap-2.5">
                              <div className="flex shrink-0 items-center -space-x-2">
                                {group.preview_members.slice(0, 4).map((m) => (
                                  <UserAvatar
                                    key={m.employee_id}
                                    avatarUrl={m.avatar_url}
                                    name={m.preferred_name || m.full_name}
                                    size="xs"
                                    className="ring-2 ring-white"
                                  />
                                ))}
                                {group.overflow_count > 0 && (
                                  <span className="inline-flex h-6 min-w-6 items-center justify-center rounded-full border border-slate-200 bg-slate-50 px-1 text-[10px] font-medium text-slate-700 tabular-nums ring-2 ring-white">
                                    +{group.overflow_count}
                                  </span>
                                )}
                              </div>
                              <span className="min-w-6 text-right text-sm font-semibold text-slate-900 tabular-nums">
                                {group.count}
                              </span>
                            </div>
                          </button>
                        )
                      })}
                    </div>
                  </CardContent>
                </Card>

                {/* Middle Column: Celebrations + Holidays + Quick Reports */}
                <div className="space-y-4 xl:col-span-4">
                  <Card className={cn(celebrations.length === 0 && 'gap-2 py-4')}>
                    <CardHeader className={cn(celebrations.length === 0 ? 'px-5 pb-0' : 'pb-2')}>
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Award className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                        {isEs ? 'Celebraciones' : '生日与周年'}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className={cn(celebrations.length === 0 && 'px-5 py-0')}>
                      {renderCelebrationList(5)}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Calendar className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                        {t('dashboard.holidays.title')}
                      </CardTitle>
                    </CardHeader>
                    <CardContent>
                      {adminData.upcoming_holidays.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          {t('dashboard.holidays.empty')}
                        </p>
                      ) : (
                        <ul className="space-y-2.5">
                          {adminData.upcoming_holidays.slice(0, 4).map((h) => {
                            const parsedDate = new Date(`${h.date}T12:00:00Z`)
                            const monthLabel = Number.isNaN(parsedDate.getTime())
                              ? h.date.slice(5, 7)
                              : isEs
                                ? new Intl.DateTimeFormat('es-ES', {
                                    month: 'short',
                                    timeZone: 'UTC',
                                  })
                                    .format(parsedDate)
                                    .replace('.', '')
                                    .toUpperCase()
                                : `${parsedDate.getUTCMonth() + 1}月`
                            const dayLabel = Number.isNaN(parsedDate.getTime())
                              ? h.date.slice(8, 10)
                              : String(parsedDate.getUTCDate()).padStart(2, '0')
                            return (
                              <li
                                key={`${h.date}-${h.name_es}`}
                                className="flex items-center justify-between gap-3 text-xs"
                              >
                                <div className="min-w-0">
                                  <div className="truncate font-medium text-slate-900">
                                    {isEs ? h.name_es : h.name_zh}
                                  </div>
                                  <div className="truncate text-[11px] text-slate-500">
                                    {isEs ? h.name_zh : h.name_es}
                                  </div>
                                </div>
                                <div className="w-11 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-white text-center">
                                  <div className="bg-[#2563EB] py-0.5 text-[10px] font-semibold tracking-wide text-white uppercase">
                                    {monthLabel}
                                  </div>
                                  <div className="py-1 text-sm leading-none font-bold text-slate-900 tabular-nums">
                                    {dayLabel}
                                  </div>
                                </div>
                              </li>
                            )
                          })}
                        </ul>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <FileText className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
                        {t('dashboard.quickReports.title')}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-1.5">
                      {adminData.quick_reports.map((rep) => (
                        <button
                          key={rep.key}
                          type="button"
                          disabled={!rep.available}
                          onClick={() => onNavigate('reports')}
                          className="flex w-full items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-left text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 hover:text-slate-900 disabled:opacity-50"
                        >
                          <span className="truncate">
                            {isEs ? rep.title_es : rep.title_zh}
                          </span>
                          <ArrowRight
                            className="h-3.5 w-3.5 shrink-0 text-slate-400"
                            aria-hidden="true"
                          />
                        </button>
                      ))}
                    </CardContent>
                  </Card>
                </div>

                {/* Right Column: Announcements */}
                <Card className="md:col-span-2 xl:col-span-4">
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Megaphone
                          className="h-4 w-4 text-[#2563EB]"
                          aria-hidden="true"
                        />
                        {t('dashboard.announcements.title')}
                      </CardTitle>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => onNavigate('announcements')}
                        className="h-7 px-2 text-xs text-[#2563EB] hover:text-blue-700"
                      >
                        {t('dashboard.announcements.viewAll')}
                      </Button>
                    </div>
                    <div className="mt-2 flex gap-1">
                      {(['all', 'notice', 'policy'] as const).map((tab) => (
                        <button
                          key={tab}
                          type="button"
                          onClick={() => setAnnouncementTab(tab)}
                          className={cn(
                            'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                            announcementTab === tab
                              ? 'bg-[#2563EB] text-white'
                              : 'bg-slate-100 text-slate-600 hover:text-slate-900',
                          )}
                        >
                          {t(`announcements.categories.${tab}`)}
                        </button>
                      ))}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {filteredAdminAnnouncements.length === 0 ? (
                      <div className="flex flex-col items-center gap-3 py-6 text-center">
                        <p className="text-xs text-slate-500">
                          {t('dashboard.announcements.empty')}
                        </p>
                        <Button
                          type="button"
                          size="sm"
                          onClick={() => onNavigate('announcements')}
                          className="h-8 bg-[#2563EB] text-xs text-white hover:bg-blue-700"
                        >
                          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                          {isEs ? 'Nuevo comunicado' : '发布新公告'}
                        </Button>
                      </div>
                    ) : (
                      filteredAdminAnnouncements.map((ann) => (
                        <button
                          key={ann.id}
                          type="button"
                          onClick={() => void openAnnouncementDetail(ann.id)}
                          className="w-full rounded-lg border border-slate-200/80 p-3 text-left transition-colors hover:bg-slate-50"
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex min-w-0 items-center gap-1.5">
                              {ann.is_pinned && (
                                <Pin
                                  className="h-3.5 w-3.5 shrink-0 text-[#2563EB]"
                                  aria-hidden="true"
                                />
                              )}
                              <span className="truncate text-xs font-semibold text-slate-900">
                                {ann.title}
                              </span>
                            </div>
                            <Badge
                              variant="outline"
                              className="shrink-0 border-slate-200 bg-slate-50 text-[10px]"
                            >
                              {t(`announcements.categories.${ann.category}`)}
                            </Badge>
                          </div>
                          <div className="mt-1 flex items-center justify-between text-[11px] text-slate-500 tabular-nums">
                            <span>
                              {formatUtcDateTime(ann.published_at_utc, locale)}
                            </span>
                            {ann.requires_acknowledgement && (
                              <span>
                                {ann.acknowledged_count}/{ann.target_count}
                              </span>
                            )}
                          </div>
                        </button>
                      ))
                    )}
                  </CardContent>
                </Card>
              </div>
            )
          )}
        </div>
      )}

      {/* EMPLOYEE VIEW (Personio 2-Column Layout) */}
      {(viewMode === 'employee' || isEmployeeRole) && (
        <div className="space-y-4">
          {myLoading && !myDashboard ? (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
              <div className="space-y-4 lg:col-span-8">
                <Skeleton className="h-44 rounded-xl" />
                <Skeleton className="h-44 rounded-xl" />
              </div>
              <div className="space-y-4 lg:col-span-4">
                <Skeleton className="h-80 rounded-xl" />
                <Skeleton className="h-44 rounded-xl" />
              </div>
            </div>
          ) : !myDashboard || !myDashboard.has_employee_profile ? (
            <EmptyState
              icon={Users}
              title={t('dashboard.noEmployeeProfileTitle')}
              description={t('dashboard.noEmployeeProfileDesc')}
              actionLabel={t('nav.employees')}
              onAction={() => onNavigate('employees')}
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
              {/* Left Wide Column (lg:col-span-8) */}
              <div className="space-y-5 lg:col-span-8">
                {/* 1. Destacados (Highlights & Approvals) */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <Bell
                        className="h-4 w-4 text-[#2563EB]"
                        aria-hidden="true"
                      />
                      {isEs ? 'Destacados' : '待办与高亮'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2.5">
                    {/* Pending Leaves (Approver) */}
                    {adminData && adminData.pending_leaves_count > 0 && (
                      <button
                        type="button"
                        onClick={() => onNavigate('leaves')}
                        className="flex w-full items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50/60 px-3.5 py-2.5 text-left transition-colors hover:bg-blue-50"
                      >
                        <div className="flex items-center gap-2.5">
                          <CalendarClock
                            className="h-4 w-4 shrink-0 text-[#2563EB]"
                            aria-hidden="true"
                          />
                          <span className="text-xs font-medium text-slate-900 tabular-nums">
                            {t('dashboard.pendingLeavesBadge', {
                              count: adminData.pending_leaves_count,
                            })}
                          </span>
                        </div>
                        <ArrowRight
                          className="h-4 w-4 shrink-0 text-[#2563EB]"
                          aria-hidden="true"
                        />
                      </button>
                    )}

                    {/* Pending Announcements */}
                    {myDashboard.pending_announcements.map((ann) => (
                      <div
                        key={ann.id}
                        className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50/50 px-3.5 py-2.5"
                      >
                        <button
                          type="button"
                          onClick={() => void openAnnouncementDetail(ann.id)}
                          className="min-w-0 flex-1 text-left"
                        >
                          <div className="truncate text-xs font-semibold text-slate-900">
                            {ann.title}
                          </div>
                          <div className="text-[11px] text-slate-500 tabular-nums">
                            {formatUtcDateTime(ann.published_at_utc, locale)}
                          </div>
                        </button>
                        <Button
                          type="button"
                          size="sm"
                          disabled={ackBusyId === ann.id}
                          onClick={() =>
                            void handleAcknowledgeAnnouncement(ann.id)
                          }
                          className="h-7 shrink-0 bg-[#2563EB] px-2.5 text-xs text-white hover:bg-blue-700"
                        >
                          <ShieldCheck
                            className="h-3.5 w-3.5"
                            aria-hidden="true"
                          />
                          {t('announcements.acknowledgeBtn')}
                        </Button>
                      </div>
                    ))}

                    {/* Actionable Notifications */}
                    {myDashboard.notifications.map((notif, idx) => (
                      <button
                        key={`${notif.kind}-${notif.local_date}-${idx}`}
                        type="button"
                        onClick={() =>
                          onNavigate(
                            notif.kind.includes('leave')
                              ? 'leaves'
                              : notif.kind.includes('doc')
                                ? 'documents'
                                : 'attendance',
                          )
                        }
                        className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3.5 py-2.5 text-left transition-colors hover:bg-slate-100"
                      >
                        <div className="flex items-center gap-2.5">
                          <AlertTriangle
                            className="h-4 w-4 shrink-0 text-amber-600"
                            aria-hidden="true"
                          />
                          <span className="text-xs font-medium text-slate-800">
                            {notif.message}
                          </span>
                        </div>
                        <ArrowRight
                          className="h-3.5 w-3.5 shrink-0 text-slate-400"
                          aria-hidden="true"
                        />
                      </button>
                    ))}

                    {myDashboard.pending_announcements.length === 0 &&
                      myDashboard.notifications.length === 0 &&
                      (!adminData || adminData.pending_leaves_count === 0) && (
                        <div className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50/50 px-3.5 py-3 text-xs text-slate-500">
                          <CheckCircle2
                            className="h-4 w-4 shrink-0 text-emerald-600"
                            aria-hidden="true"
                          />
                          <span>
                            {isEs
                              ? 'Todo al día. No tiene tareas pendientes.'
                              : '全部已处理，当前暂无待办事项。'}
                          </span>
                        </div>
                      )}
                  </CardContent>
                </Card>

                {/* 2. Esta semana (Holidays + Recent Documents / Announcements) */}
                <Card>
                  <CardHeader className="pb-2">
                    <div className="flex items-center justify-between gap-2">
                      <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                        <Calendar
                          className="h-4 w-4 text-[#2563EB]"
                          aria-hidden="true"
                        />
                        {isEs ? 'Esta semana' : '本周日程与动态'}
                      </CardTitle>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => onNavigate('schedule')}
                        className="h-7 px-2 text-xs text-[#2563EB] hover:text-blue-700"
                      >
                        {t('nav.schedule')}
                      </Button>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {myDashboard.upcoming_holidays.length > 0 && (
                      <div className="space-y-1.5">
                        <div className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
                          {t('dashboard.holidays.title')}
                        </div>
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {myDashboard.upcoming_holidays.slice(0, 4).map((h) => (
                            <div
                              key={`${h.date}-${h.name_es}`}
                              className="flex items-center justify-between gap-2 rounded-lg border border-slate-200/75 bg-slate-50/50 px-3 py-2 text-xs"
                            >
                              <span className="truncate font-medium text-slate-800">
                                {isEs ? h.name_es : h.name_zh}
                              </span>
                              <Badge
                                variant="outline"
                                className="shrink-0 border-slate-200 bg-white text-[11px] text-slate-700 tabular-nums"
                              >
                                {h.date}
                              </Badge>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Recent Documents quick access */}
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
                          {t('dashboard.myDocuments.title')}
                        </span>
                        <button
                          type="button"
                          onClick={() => onNavigate('documents')}
                          className="text-xs font-medium text-[#2563EB] hover:underline"
                        >
                          {t('dashboard.announcements.viewAll')}
                        </button>
                      </div>
                      {myDashboard.recent_documents.length === 0 ? (
                        <p className="text-xs text-slate-500">
                          {t('dashboard.myDocuments.empty')}
                        </p>
                      ) : (
                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                          {myDashboard.recent_documents
                            .slice(0, 4)
                            .map((item: DocumentListItemResponse) => (
                              <div
                                key={item.document.id}
                                className="flex items-center justify-between gap-2 rounded-lg border border-slate-200/75 bg-white px-3 py-2"
                              >
                                <div className="min-w-0">
                                  <div className="truncate text-xs font-medium text-slate-900">
                                    {item.document.title}
                                  </div>
                                  <div className="text-[11px] text-slate-500">
                                    {t(
                                      `documents.categories.${item.document.category}`,
                                    )}
                                  </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                  <Button
                                    type="button"
                                    size="icon-sm"
                                    variant="outline"
                                    disabled={docBusyId === item.document.id}
                                    onClick={() =>
                                      void handleDownloadDocument(
                                        item.document.id,
                                      )
                                    }
                                    aria-label={t(
                                      'documents.detailDrawer.downloadButton',
                                    )}
                                    title={t(
                                      'documents.detailDrawer.downloadButton',
                                    )}
                                  >
                                    <Download
                                      className="h-3.5 w-3.5"
                                      aria-hidden="true"
                                    />
                                  </Button>
                                  {item.document.requires_signature &&
                                    !item.signed_by_self && (
                                      <Button
                                        type="button"
                                        size="xs"
                                        disabled={docBusyId === item.document.id}
                                        onClick={() =>
                                          void handleSignDocument(
                                            item.document.id,
                                          )
                                        }
                                        className="bg-[#2563EB] text-white hover:bg-blue-700"
                                      >
                                        {t('dashboard.myDocuments.sign')}
                                      </Button>
                                    )}
                                </div>
                              </div>
                            ))}
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>

                {/* 3. Celebraciones (Birthdays & Anniversaries) */}
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
                      <Award
                        className="h-4 w-4 text-[#2563EB]"
                        aria-hidden="true"
                      />
                      {isEs ? 'Celebraciones' : '生日与周年'}
                    </CardTitle>
                  </CardHeader>
                  <CardContent>{renderCelebrationList(6)}</CardContent>
                </Card>
              </div>

              {/* Right Narrow Column (lg:col-span-4) */}
              <div className="space-y-5 lg:col-span-4">
                <WorkRingClockCard />

                {/* Personal Quick Card */}
                {myDashboard.employee && (
                  <Card className="gap-4 py-5">
                    <CardContent className="space-y-4 px-5">
                      <button
                        type="button"
                        onClick={() => onNavigate('profile')}
                        className="flex w-full items-center gap-3 text-left transition-opacity hover:opacity-85"
                      >
                        <UserAvatar
                          avatarUrl={myDashboard.employee.avatar_url}
                          name={
                            myDashboard.employee.preferred_name ||
                            myDashboard.employee.full_name
                          }
                          size="md"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-semibold text-slate-900">
                            {myDashboard.employee.full_name}
                          </div>
                          <div className="truncate text-xs text-slate-500">
                            {myDashboard.employee.job_title ||
                              myDashboard.employee.department ||
                              myDashboard.employee.store_name}
                          </div>
                        </div>
                      </button>

                      {/* 3 Non-Truncated Quick Action Buttons */}
                      <div className="grid grid-cols-3 gap-2">
                        <button
                          type="button"
                          onClick={() => onNavigate('attendance')}
                          className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-2.5 text-center transition-colors hover:border-blue-200 hover:bg-blue-50/50"
                        >
                          <Clock
                            className="h-4 w-4 text-[#2563EB]"
                            aria-hidden="true"
                          />
                          <span className="text-[11px] leading-tight font-medium text-slate-800">
                            {isEs ? 'Fichar' : '打卡'}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => onNavigate('leaves')}
                          className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-2.5 text-center transition-colors hover:border-blue-200 hover:bg-blue-50/50"
                        >
                          <CalendarClock
                            className="h-4 w-4 text-[#2563EB]"
                            aria-hidden="true"
                          />
                          <span className="text-[11px] leading-tight font-medium text-slate-800">
                            {isEs ? 'Ausencias' : '请假'}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => onNavigate('documents')}
                          className="flex flex-col items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50/70 px-2 py-2.5 text-center transition-colors hover:border-blue-200 hover:bg-blue-50/50"
                        >
                          <FileText
                            className="h-4 w-4 text-[#2563EB]"
                            aria-hidden="true"
                          />
                          <span className="text-[11px] leading-tight font-medium text-slate-800">
                            {isEs ? 'Documentos' : '文档'}
                          </span>
                        </button>
                      </div>

                      {/* Standalone Large Remaining Vacation Days Card */}
                      {myDashboard.vacation_balance && (
                        <button
                          type="button"
                          onClick={() => onNavigate('leaves')}
                          className="flex w-full items-center justify-between rounded-xl border border-blue-200/80 bg-blue-50/50 px-4 py-3 text-left transition-colors hover:bg-blue-50"
                        >
                          <div>
                            <div className="text-xs font-medium text-slate-600">
                              {isEs ? 'Vacaciones disponibles' : '剩余年假天数'}
                            </div>
                            <div className="mt-0.5 text-[11px] text-slate-500 tabular-nums">
                              {isEs ? 'Total anual' : '全年额度'}:{' '}
                              {myDashboard.vacation_balance.entitled_days} d
                            </div>
                          </div>
                          <div className="text-2xl font-bold tracking-tight text-[#2563EB] tabular-nums">
                            {myDashboard.vacation_balance.remaining_days} d
                          </div>
                        </button>
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Who's In Member Drawer */}
      <Sheet
        open={Boolean(activeWhoGroup)}
        onOpenChange={(open) => {
          if (!open) setActiveWhoGroup(null)
        }}
      >
        <SheetContent
          side="right"
          className="w-full overflow-y-auto bg-white sm:max-w-md"
        >
          <SheetHeader>
            <SheetTitle>
              {activeWhoGroup
                ? `${t(`dashboard.whoIsIn.statuses.${activeWhoGroup.status}`)} (${activeWhoGroup.count})`
                : ''}
            </SheetTitle>
            <SheetDescription>
              {t('dashboard.whoIsIn.drawerDescription')}
            </SheetDescription>
          </SheetHeader>
          <div className="mt-4 space-y-2 px-4">
            {activeWhoGroup && activeWhoGroup.members.length === 0 ? (
              <p className="py-6 text-center text-xs text-slate-500">
                {t('dashboard.whoIsIn.emptyGroup')}
              </p>
            ) : (
              activeWhoGroup?.members.map((m) => (
                <button
                  key={m.employee_id}
                  type="button"
                  onClick={() => {
                    setActiveWhoGroup(null)
                    onOpenEmployeeProfile?.(m.employee_id)
                  }}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-slate-200 p-2.5 text-left transition-colors hover:bg-slate-50"
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <UserAvatar
                      avatarUrl={m.avatar_url}
                      name={m.preferred_name || m.full_name}
                      size="sm"
                    />
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-slate-900">
                        {m.full_name}
                      </div>
                      <div className="truncate text-xs text-slate-500 tabular-nums">
                        {m.employee_code} · {m.store_name}
                        {m.department ? ` · ${m.department}` : ''}
                      </div>
                    </div>
                  </div>
                  {m.since_utc && (
                    <span className="shrink-0 text-xs text-slate-500 tabular-nums">
                      {formatUtcDateTime(m.since_utc, locale)}
                    </span>
                  )}
                </button>
              ))
            )}
          </div>
        </SheetContent>
      </Sheet>

      {/* Announcement Detail Drawer */}
      <Sheet
        open={Boolean(selectedAnnouncement)}
        onOpenChange={(open) => {
          if (!open) setSelectedAnnouncement(null)
        }}
      >
        <SheetContent
          side="right"
          className="w-full overflow-y-auto bg-white sm:max-w-lg"
        >
          <SheetHeader>
            <SheetTitle>{selectedAnnouncement?.title}</SheetTitle>
            <SheetDescription>
              {selectedAnnouncement &&
                formatUtcDateTime(
                  selectedAnnouncement.published_at_utc,
                  locale,
                )}
            </SheetDescription>
          </SheetHeader>
          {selectedAnnouncement && (
            <div className="mt-4 space-y-4 px-4">
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline" className="border-slate-200 bg-slate-50">
                  {t(`announcements.categories.${selectedAnnouncement.category}`)}
                </Badge>
                {selectedAnnouncement.requires_acknowledgement && (
                  <Badge
                    variant="outline"
                    className="border-blue-200 bg-blue-50 text-[#2563EB] tabular-nums"
                  >
                    {selectedAnnouncement.acknowledged_count}/
                    {selectedAnnouncement.target_count}
                  </Badge>
                )}
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm whitespace-pre-wrap text-slate-800">
                {selectedAnnouncement.body}
              </div>

              {selectedAnnouncement.attachment_document_id && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={annLoading}
                  onClick={() =>
                    void handleAnnouncementAttachment(selectedAnnouncement.id)
                  }
                >
                  <Paperclip className="h-4 w-4" aria-hidden="true" />
                  {t('announcements.downloadAttachment')}
                </Button>
              )}

              {selectedAnnouncement.requires_acknowledgement && (
                <div className="pt-2">
                  {selectedAnnouncement.acknowledged_by_self ? (
                    <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
                      <CheckCircle2
                        className="h-4 w-4 shrink-0"
                        aria-hidden="true"
                      />
                      <span>{t('announcements.alreadyAcknowledged')}</span>
                    </div>
                  ) : (
                    <Button
                      type="button"
                      disabled={ackBusyId === selectedAnnouncement.id}
                      onClick={() =>
                        void handleAcknowledgeAnnouncement(
                          selectedAnnouncement.id,
                        )
                      }
                      className="w-full bg-[#2563EB] text-white hover:bg-blue-700"
                    >
                      <ShieldCheck className="h-4 w-4" aria-hidden="true" />
                      {t('announcements.acknowledgeBtn')}
                    </Button>
                  )}
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
