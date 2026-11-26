import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  Briefcase,
  Building2,
  CalendarClock,
  Camera,
  Clock,
  CreditCard,
  Download,
  Eye,
  EyeOff,
  FileSpreadsheet,
  FileText,
  Lock,
  MapPin,
  PhoneCall,
  ShieldAlert,
  User,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  ContractType,
  DocumentListItemResponse,
  EmployeeResponse,
  EmployeeSensitivePiiResponse,
  EmploymentStatus,
  StoreResponse,
  WorkScheduleType,
} from '@/api/types'
import { EmptyState } from '@/components/common/EmptyState'
import { UserAvatar } from '@/components/common/UserAvatar'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
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
import { useAuth } from '@/features/auth/AuthContext'
import { AvatarCropperDialog } from '@/features/employees/AvatarCropperDialog'
import { ProfileAttendanceTab } from '@/features/employees/ProfileAttendanceTab'
import { ProfileDocumentsTab } from '@/features/employees/ProfileDocumentsTab'
import { ProfileLeavesTab } from '@/features/employees/ProfileLeavesTab'
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'
import { cn } from '@/lib/utils'

const REVEAL_DURATION_SECONDS = 15

const STATUS_BADGE_CLASS: Record<EmploymentStatus, string> = {
  active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  on_leave: 'border-amber-200 bg-amber-50 text-amber-800',
  terminated: 'border-slate-200 bg-slate-100 text-slate-600',
  legal_hold: 'border-rose-200 bg-rose-50 text-rose-700',
  anonymized: 'border-slate-200 bg-slate-100 text-slate-500',
}

type PrimaryTab = 'personal' | 'documents' | 'attendance' | 'leaves'

type PersonalSubTab = 'public' | 'hr'

interface EmployeeProfilePageProps {
  employeeId: string
  stores?: StoreResponse[]
  onBack?: () => void
  backLabel?: string
}

function computeTenureText(hireDateIso: string, isEs: boolean): string {
  const start = new Date(`${hireDateIso}T00:00:00Z`)
  if (Number.isNaN(start.getTime())) return ''
  const now = new Date()
  let months =
    (now.getUTCFullYear() - start.getUTCFullYear()) * 12 +
    (now.getUTCMonth() - start.getUTCMonth())
  if (now.getUTCDate() < start.getUTCDate()) {
    months -= 1
  }
  months = Math.max(0, months)
  const years = Math.floor(months / 12)
  const remMonths = months % 12
  if (isEs) {
    if (years > 0 && remMonths > 0) return `${years}a ${remMonths}m`
    if (years > 0) return `${years} años`
    return `${remMonths} meses`
  }
  if (years > 0 && remMonths > 0) return `${years}年${remMonths}个月`
  if (years > 0) return `${years}年`
  return `${remMonths}个月`
}

export function EmployeeProfilePage({
  employeeId,
  stores: propStores,
  onBack,
  backLabel,
}: EmployeeProfilePageProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant, profile } = useAuth()
  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const role = activeTenant?.role

  const [stores, setStores] = useState<StoreResponse[]>(propStores ?? [])
  const [employee, setEmployee] = useState<EmployeeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [savedNotice, setSavedNotice] = useState(false)

  const [primaryTab, setPrimaryTab] = useState<PrimaryTab>('personal')
  const [personalSubTab, setPersonalSubTab] =
    useState<PersonalSubTab>('public')
  const [isEditing, setIsEditing] = useState(false)

  // Editable fields
  const [primaryStoreId, setPrimaryStoreId] = useState('')
  const [employeeCode, setEmployeeCode] = useState('')
  const [fullName, setFullName] = useState('')
  const [preferredName, setPreferredName] = useState('')
  const [jobTitle, setJobTitle] = useState('')
  const [department, setDepartment] = useState('')
  const [email, setEmail] = useState('')
  const [avatarDialogOpen, setAvatarDialogOpen] = useState(false)
  const [phone, setPhone] = useState('')
  const [birthDate, setBirthDate] = useState('')
  const [hireDate, setHireDate] = useState('')
  const [contractType, setContractType] = useState<ContractType>('indefinido')
  const [scheduleType, setScheduleType] =
    useState<WorkScheduleType>('full_time')
  const [weeklyHours, setWeeklyHours] = useState('40.00')
  const [vacationDays, setVacationDays] = useState('30')
  const [empStatus, setEmpStatus] = useState<EmploymentStatus>('active')
  const [emergencyContactName, setEmergencyContactName] = useState('')
  const [emergencyContactPhone, setEmergencyContactPhone] = useState('')

  // Optional PII rotation inputs
  const [newNieDni, setNewNieDni] = useState('')
  const [newIban, setNewIban] = useState('')
  const [newNaf, setNewNaf] = useState('')

  // On-demand 15-second ephemeral plaintext PII viewer
  const [decryptedPii, setDecryptedPii] =
    useState<EmployeeSensitivePiiResponse | null>(null)
  const [revealSecondsLeft, setRevealSecondsLeft] = useState(0)
  const [revealing, setRevealing] = useState(false)

  // Payslips (Nóminas) inside HR info
  const [payslips, setPayslips] = useState<DocumentListItemResponse[]>([])
  const [payslipsLoading, setPayslipsLoading] = useState(false)
  const [payslipsError, setPayslipsError] = useState<string | null>(null)
  const [downloadingPayslipId, setDownloadingPayslipId] = useState<
    string | null
  >(null)

  // Termination & Anonymization dialogs
  const [terminationDate, setTerminationDate] = useState(() =>
    new Date().toISOString().slice(0, 10),
  )
  const [terminateDialogOpen, setTerminateDialogOpen] = useState(false)
  const [anonymizeDialogOpen, setAnonymizeDialogOpen] = useState(false)

  const syncForm = (data: EmployeeResponse) => {
    setPrimaryStoreId(data.primary_store_id)
    setEmployeeCode(data.employee_code)
    setFullName(data.full_name)
    setPreferredName(data.preferred_name)
    setJobTitle(data.job_title ?? '')
    setDepartment(data.department ?? '')
    setEmail(data.email)
    setPhone(data.phone)
    setBirthDate(data.birth_date ?? '')
    setHireDate(data.hire_date)
    setContractType(data.contract_type)
    setScheduleType(data.work_schedule_type)
    setWeeklyHours(String(data.contracted_weekly_hours))
    setVacationDays(String(data.annual_vacation_days))
    setEmpStatus(data.employment_status)
    setEmergencyContactName(data.emergency_contact_name ?? '')
    setEmergencyContactPhone(data.emergency_contact_phone ?? '')
    setNewNieDni('')
    setNewIban('')
    setNewNaf('')
  }

  const loadEmployee = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const [empRes, storeRes] = await Promise.all([
        apiClient.GET('/api/v1/employees/{employee_id}', {
          params: { path: { employee_id: employeeId } },
        }),
        propStores && propStores.length > 0
          ? Promise.resolve(null)
          : apiClient.GET('/api/v1/stores'),
      ])
      if (empRes.error || !empRes.data) {
        setErrorBanner(extractApiError(empRes.error, t('common.error')))
        return
      }
      setEmployee(empRes.data)
      syncForm(empRes.data)
      if (storeRes && storeRes.data) {
        setStores(storeRes.data.items)
      }
    } finally {
      setLoading(false)
    }
  }, [employeeId, propStores, t])

  const loadPayslips = useCallback(async () => {
    setPayslipsLoading(true)
    setPayslipsError(null)
    try {
      const res = await apiClient.GET('/api/v1/documents', {
        params: {
          query: {
            employee_id: employeeId,
            category: 'payslip',
          },
        },
      })
      if (res.error || !res.data) {
        setPayslipsError(extractApiError(res.error, t('common.error')))
        return
      }
      setPayslips(res.data.items)
    } catch (err) {
      setPayslipsError(extractApiError(err, t('common.error')))
    } finally {
      setPayslipsLoading(false)
    }
  }, [employeeId, t])

  useEffect(() => {
    void loadEmployee()
    void loadPayslips()
  }, [loadEmployee, loadPayslips])

  const handleDownloadPayslip = async (docId: string) => {
    setDownloadingPayslipId(docId)
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
      setDownloadingPayslipId(null)
    }
  }

  useEffect(() => {
    if (revealSecondsLeft <= 0) {
      setDecryptedPii(null)
      return
    }
    const timer = window.setInterval(() => {
      setRevealSecondsLeft((prev) => (prev <= 1 ? 0 : prev - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [revealSecondsLeft])

  const isFrozen =
    employee?.employment_status === 'legal_hold' ||
    employee?.employment_status === 'anonymized' ||
    employee?.employment_status === 'terminated'

  const canManage = (role === 'owner' || role === 'hr') && !isFrozen
  const canManageLifecycle = role === 'owner' || role === 'hr'
  const canEditAvatar =
    canManage ||
    (employee?.user_id !== null && employee?.user_id === profile?.user_id)
  const canDecryptPii =
    role === 'owner' ||
    role === 'hr' ||
    role === 'accountant' ||
    (role === 'employee' &&
      employee?.user_id !== null &&
      employee?.user_id === profile?.user_id)

  const handleRevealPii = async () => {
    if (!canDecryptPii) return
    setRevealing(true)
    setErrorBanner(null)
    try {
      const { data, error } = await apiClient.GET(
        '/api/v1/employees/{employee_id}/sensitive',
        {
          params: { path: { employee_id: employeeId } },
        },
      )
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setDecryptedPii(data)
      setRevealSecondsLeft(REVEAL_DURATION_SECONDS)
    } finally {
      setRevealing(false)
    }
  }

  const handleSave = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!canManage) return
    setSaving(true)
    setErrorBanner(null)
    setSavedNotice(false)
    try {
      const { data, error } = await apiClient.PATCH(
        '/api/v1/employees/{employee_id}',
        {
          params: { path: { employee_id: employeeId } },
          body: {
            primary_store_id: primaryStoreId,
            employee_code: employeeCode,
            full_name: fullName,
            preferred_name: preferredName,
            job_title: jobTitle.trim(),
            department: department.trim(),
            email,
            phone,
            birth_date: birthDate || null,
            hire_date: hireDate,
            contract_type: contractType,
            work_schedule_type: scheduleType,
            contracted_weekly_hours: Number(weeklyHours),
            annual_vacation_days: Number(vacationDays),
            employment_status: empStatus,
            user_id: employee?.user_id ?? null,
            emergency_contact_name: emergencyContactName.trim(),
            emergency_contact_phone: emergencyContactPhone.trim(),
            nie_dni: newNieDni.trim() ? newNieDni.trim() : null,
            iban: newIban.trim() ? newIban.trim() : null,
            naf: newNaf.trim() ? newNaf.trim() : null,
          },
        },
      )
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setEmployee(data)
      syncForm(data)
      setDecryptedPii(null)
      setRevealSecondsLeft(0)
      setIsEditing(false)
      setSavedNotice(true)
    } finally {
      setSaving(false)
    }
  }

  const handleTerminateConfirm = async () => {
    const { data, error } = await apiClient.POST(
      '/api/v1/employees/{employee_id}/terminate',
      {
        params: { path: { employee_id: employeeId } },
        body: { termination_date: terminationDate },
      },
    )
    setTerminateDialogOpen(false)
    if (error || !data) {
      setErrorBanner(extractApiError(error, t('common.error')))
      return
    }
    setEmployee(data)
    syncForm(data)
  }

  const handleAnonymizeConfirm = async () => {
    const { data, error } = await apiClient.POST(
      '/api/v1/employees/{employee_id}/anonymize',
      {
        params: { path: { employee_id: employeeId } },
      },
    )
    setAnonymizeDialogOpen(false)
    if (error || !data) {
      setErrorBanner(extractApiError(error, t('common.error')))
      return
    }
    setEmployee(data)
    syncForm(data)
    setDecryptedPii(null)
    setRevealSecondsLeft(0)
  }

  const retentionExpired =
    employee?.retention_until != null &&
    new Date(employee.retention_until).getTime() <= Date.now()

  const storeName = useMemo(() => {
    if (!employee) return '—'
    const st = stores.find((s) => s.id === employee.primary_store_id)
    return st ? `${st.code} · ${st.name}` : '—'
  }, [employee, stores])

  const primaryTabs: Array<{
    id: PrimaryTab
    label: string
    icon: typeof User
  }> = [
    {
      id: 'personal',
      label: isEs ? 'Información personal' : '基本资料',
      icon: User,
    },
    {
      id: 'documents',
      label: isEs ? 'Documentos' : '文档',
      icon: FileText,
    },
    {
      id: 'attendance',
      label: isEs ? 'Registro de jornada' : '考勤记录',
      icon: Clock,
    },
    {
      id: 'leaves',
      label: isEs ? 'Ausencias' : '假期',
      icon: CalendarClock,
    },
  ]

  const personalSubTabs: Array<{
    id: PersonalSubTab
    label: string
    icon: typeof User
  }> = [
    {
      id: 'public',
      label: isEs ? 'Perfil público' : '公开资料',
      icon: User,
    },
    {
      id: 'hr',
      label: isEs ? 'Información RR. HH.' : '人事信息',
      icon: Briefcase,
    },
  ]

  return (
    <div className="space-y-5">
      {onBack && (
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onBack}
            className="bg-white"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {backLabel ?? t('employees.profile.backToList')}
          </Button>
        </div>
      )}

      {loading ? (
        <Skeleton className="h-36 w-full rounded-xl" />
      ) : (
        employee && (
          <>
            {/* Rich Header Banner */}
            <div className="rounded-xl border border-slate-200/75 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-4">
                  <div className="relative shrink-0">
                    <UserAvatar
                      avatarUrl={employee.avatar_url}
                      name={employee.preferred_name || employee.full_name}
                      size="lg"
                      className="h-16 w-16 text-lg"
                    />
                    {canEditAvatar && (
                      <button
                        type="button"
                        onClick={() => setAvatarDialogOpen(true)}
                        aria-label={t('employees.avatar.editBtn')}
                        title={t('employees.avatar.editBtn')}
                        className="absolute -right-1 -bottom-1 flex h-7 w-7 items-center justify-center rounded-full border border-white bg-[#2563EB] text-white shadow-xs transition-colors hover:bg-blue-700"
                      >
                        <Camera className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    )}
                  </div>

                  <div className="min-w-0 space-y-1.5">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <h1 className="text-xl font-semibold tracking-tight text-slate-900">
                        {employee.full_name}
                      </h1>
                      <Badge
                        variant="outline"
                        className={STATUS_BADGE_CLASS[employee.employment_status]}
                      >
                        {t(
                          `employees.employmentStatuses.${employee.employment_status}`,
                        )}
                      </Badge>
                      <span className="font-mono text-xs text-slate-500 tabular-nums">
                        {employee.employee_code}
                      </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
                      {employee.job_title && (
                        <span className="inline-flex items-center gap-1.5">
                          <Briefcase
                            className="h-3.5 w-3.5 text-slate-400"
                            aria-hidden="true"
                          />
                          {employee.job_title}
                        </span>
                      )}
                      {employee.department && (
                        <span className="inline-flex items-center gap-1.5">
                          <Users
                            className="h-3.5 w-3.5 text-slate-400"
                            aria-hidden="true"
                          />
                          {employee.department}
                        </span>
                      )}
                      <span className="inline-flex items-center gap-1.5">
                        <MapPin
                          className="h-3.5 w-3.5 text-slate-400"
                          aria-hidden="true"
                        />
                        {storeName}
                      </span>
                      <span className="inline-flex items-center gap-1.5">
                        <Building2
                          className="h-3.5 w-3.5 text-slate-400"
                          aria-hidden="true"
                        />
                        {activeTenant?.tenant_trade_name}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex shrink-0 flex-col items-start gap-1 rounded-lg border border-slate-200/75 bg-slate-50/70 px-3.5 py-2 text-xs sm:items-end">
                  <div className="text-slate-500 tabular-nums">
                    {isEs ? 'Alta' : '入职日期'}:{' '}
                    <span className="font-medium text-slate-800">
                      {employee.hire_date}
                    </span>
                  </div>
                  <div className="text-slate-500 tabular-nums">
                    {isEs ? 'Antigüedad' : '司龄'}:{' '}
                    <span className="font-semibold text-[#2563EB]">
                      {computeTenureText(employee.hire_date, isEs)}
                    </span>
                  </div>
                </div>
              </div>

              {/* Horizontal Primary Tabs */}
              <div className="mt-5 -mb-5 flex items-center gap-5 overflow-x-auto border-t border-slate-100 pt-1">
                {primaryTabs.map((tab) => {
                  const Icon = tab.icon
                  const isActive = primaryTab === tab.id
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setPrimaryTab(tab.id)}
                      className={cn(
                        'inline-flex shrink-0 items-center gap-1.5 border-b-2 px-1 py-3 text-xs font-medium whitespace-nowrap transition-colors',
                        isActive
                          ? 'border-[#2563EB] text-[#2563EB]'
                          : 'border-transparent text-slate-600 hover:text-slate-900',
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                      <span>{tab.label}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {isFrozen && (
              <div className="flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">
                <Lock className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{t('employees.profile.frozenBanner')}</span>
              </div>
            )}

            {errorBanner && (
              <div
                role="alert"
                className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700"
              >
                {errorBanner}
              </div>
            )}

            {savedNotice && (
              <div
                role="status"
                className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700"
              >
                {t('employees.profile.savedSuccess')}
              </div>
            )}

            {/* Tab Content */}
            {primaryTab === 'documents' && (
              <ProfileDocumentsTab employee={employee} />
            )}

            {primaryTab === 'attendance' && (
              <ProfileAttendanceTab employee={employee} />
            )}

            {primaryTab === 'leaves' && (
              <ProfileLeavesTab employee={employee} />
            )}

            {primaryTab === 'personal' && (
              <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
                {/* Left Vertical Secondary Menu */}
                <aside className="lg:col-span-3">
                  <nav
                    aria-label={isEs ? 'Secciones personales' : '资料分类'}
                    className="flex flex-row gap-1 overflow-x-auto rounded-xl border border-slate-200/75 bg-white p-2 shadow-[0_1px_2px_rgba(15,23,42,0.04)] lg:flex-col"
                  >
                    {personalSubTabs.map((sub) => {
                      const Icon = sub.icon
                      const active = personalSubTab === sub.id
                      return (
                        <button
                          key={sub.id}
                          type="button"
                          onClick={() => setPersonalSubTab(sub.id)}
                          className={cn(
                            'flex shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium transition-colors',
                            active
                              ? 'bg-blue-50 text-[#2563EB]'
                              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
                          )}
                        >
                          <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                          <span className="whitespace-nowrap">{sub.label}</span>
                        </button>
                      )
                    })}
                  </nav>
                </aside>

                {/* Right Key-Value View / Edit Panel */}
                <div className="space-y-4 lg:col-span-9">
                  <form
                    onSubmit={handleSave}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && isImeComposing(e)) {
                        e.preventDefault()
                      }
                    }}
                  >
                    <Card className="gap-4 py-5">
                      <CardHeader className="flex flex-row items-center justify-between gap-2 border-b border-slate-100 px-6 pb-3">
                        <CardTitle className="text-sm font-semibold text-slate-900">
                          {
                            personalSubTabs.find((s) => s.id === personalSubTab)
                              ?.label
                          }
                        </CardTitle>
                        {canManage && !isEditing && (
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => {
                              setSavedNotice(false)
                              setIsEditing(true)
                            }}
                            className="h-8 text-xs"
                          >
                            {isEs ? 'Modificar' : '修改'}
                          </Button>
                        )}
                      </CardHeader>

                      <CardContent className="space-y-4 px-6">
                        {personalSubTab === 'public' && (
                          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.fullName')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  required
                                  value={fullName}
                                  onChange={(e) => setFullName(e.target.value)}
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {employee.full_name}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.preferredName')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  value={preferredName}
                                  onChange={(e) =>
                                    setPreferredName(e.target.value)
                                  }
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {employee.preferred_name || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {isEs ? 'Puesto' : '职位'}
                              </Label>
                              {isEditing ? (
                                <Input
                                  value={jobTitle}
                                  onChange={(e) => setJobTitle(e.target.value)}
                                  placeholder={
                                    isEs ? 'Camarero / Chef' : '店长 / 厨师'
                                  }
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {employee.job_title || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.department')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  value={department}
                                  onChange={(e) =>
                                    setDepartment(e.target.value)
                                  }
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {employee.department || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.email')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  type="email"
                                  value={email}
                                  onChange={(e) => setEmail(e.target.value)}
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {employee.email || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.phone')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  value={phone}
                                  onChange={(e) => setPhone(e.target.value)}
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900 tabular-nums">
                                  {employee.phone || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.birthDate')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  type="date"
                                  className="tabular-nums"
                                  value={birthDate}
                                  onChange={(e) => setBirthDate(e.target.value)}
                                />
                              ) : (
                                <div className="text-sm font-medium text-slate-900 tabular-nums">
                                  {employee.birth_date || '—'}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.primaryStore')}
                              </Label>
                              {isEditing ? (
                                <Select
                                  value={primaryStoreId}
                                  onValueChange={setPrimaryStoreId}
                                >
                                  <SelectTrigger className="w-full">
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
                              ) : (
                                <div className="text-sm font-medium text-slate-900">
                                  {storeName}
                                </div>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <Label className="text-xs text-slate-500">
                                {t('employees.fields.code')}
                              </Label>
                              {isEditing ? (
                                <Input
                                  required
                                  className="uppercase tabular-nums"
                                  value={employeeCode}
                                  onChange={(e) =>
                                    setEmployeeCode(e.target.value)
                                  }
                                />
                              ) : (
                                <div className="font-mono text-sm font-medium text-slate-900 tabular-nums">
                                  {employee.employee_code}
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {personalSubTab === 'hr' && (
                          <div className="space-y-5">
                            {/* Section 1: Contract & Position */}
                            <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4">
                              <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                                <Briefcase
                                  className="h-3.5 w-3.5 text-[#2563EB]"
                                  aria-hidden="true"
                                />
                                {t('employees.profile.hrSections.contract')}
                              </h3>
                              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.hireDate')}
                                  </Label>
                                  {isEditing ? (
                                    <Input
                                      type="date"
                                      required
                                      className="bg-white tabular-nums"
                                      value={hireDate}
                                      onChange={(e) =>
                                        setHireDate(e.target.value)
                                      }
                                    />
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900 tabular-nums">
                                      {employee.hire_date}
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.status')}
                                  </Label>
                                  {isEditing ? (
                                    <Select
                                      value={empStatus}
                                      onValueChange={(v) =>
                                        setEmpStatus(v as EmploymentStatus)
                                      }
                                    >
                                      <SelectTrigger className="w-full bg-white">
                                        <SelectValue />
                                      </SelectTrigger>
                                      <SelectContent>
                                        {(
                                          [
                                            'active',
                                            'on_leave',
                                          ] as EmploymentStatus[]
                                        ).map((st) => (
                                          <SelectItem key={st} value={st}>
                                            {t(
                                              `employees.employmentStatuses.${st}`,
                                            )}
                                          </SelectItem>
                                        ))}
                                      </SelectContent>
                                    </Select>
                                  ) : (
                                    <div>
                                      <Badge
                                        variant="outline"
                                        className={
                                          STATUS_BADGE_CLASS[
                                            employee.employment_status
                                          ]
                                        }
                                      >
                                        {t(
                                          `employees.employmentStatuses.${employee.employment_status}`,
                                        )}
                                      </Badge>
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.contractType')}
                                  </Label>
                                  {isEditing ? (
                                    <Select
                                      value={contractType}
                                      onValueChange={(v) =>
                                        setContractType(v as ContractType)
                                      }
                                    >
                                      <SelectTrigger className="w-full bg-white">
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
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900">
                                      {t(
                                        `employees.contractTypes.${employee.contract_type}`,
                                      )}
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.scheduleType')}
                                  </Label>
                                  {isEditing ? (
                                    <Select
                                      value={scheduleType}
                                      onValueChange={(v) =>
                                        setScheduleType(v as WorkScheduleType)
                                      }
                                    >
                                      <SelectTrigger className="w-full bg-white">
                                        <SelectValue />
                                      </SelectTrigger>
                                      <SelectContent>
                                        <SelectItem value="full_time">
                                          {t(
                                            'employees.scheduleTypes.full_time',
                                          )}
                                        </SelectItem>
                                        <SelectItem value="part_time">
                                          {t(
                                            'employees.scheduleTypes.part_time',
                                          )}
                                        </SelectItem>
                                      </SelectContent>
                                    </Select>
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900">
                                      {t(
                                        `employees.scheduleTypes.${employee.work_schedule_type}`,
                                      )}
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.weeklyHours')}
                                  </Label>
                                  {isEditing ? (
                                    <Input
                                      type="number"
                                      step="0.5"
                                      min="1"
                                      max="40"
                                      required
                                      className="bg-white tabular-nums"
                                      value={weeklyHours}
                                      onChange={(e) =>
                                        setWeeklyHours(e.target.value)
                                      }
                                    />
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900 tabular-nums">
                                      {employee.contracted_weekly_hours} h /{' '}
                                      {isEs ? 'semana' : '周'}
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.vacationDays')}
                                  </Label>
                                  {isEditing ? (
                                    <Input
                                      type="number"
                                      min="30"
                                      max="60"
                                      required
                                      className="bg-white tabular-nums"
                                      value={vacationDays}
                                      onChange={(e) =>
                                        setVacationDays(e.target.value)
                                      }
                                    />
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900 tabular-nums">
                                      {employee.annual_vacation_days}{' '}
                                      {isEs ? 'días / año' : '天 / 年'}
                                    </div>
                                  )}
                                </div>
                              </div>
                            </section>

                            {/* Section 2: Payroll, Payslips & Social Security */}
                            <section className="space-y-4 rounded-xl border border-slate-200/80 bg-slate-50/40 p-4">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                                  <FileSpreadsheet
                                    className="h-3.5 w-3.5 text-[#2563EB]"
                                    aria-hidden="true"
                                  />
                                  {t('employees.profile.hrSections.payrollSs')}
                                </h3>
                                {canDecryptPii && (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={revealing}
                                    onClick={() => {
                                      if (decryptedPii) {
                                        setDecryptedPii(null)
                                        setRevealSecondsLeft(0)
                                      } else {
                                        void handleRevealPii()
                                      }
                                    }}
                                    className="h-7 bg-white text-xs tabular-nums"
                                  >
                                    {decryptedPii ? (
                                      <>
                                        <EyeOff
                                          className="h-3.5 w-3.5"
                                          aria-hidden="true"
                                        />
                                        {t('employees.pii.hideButton')} (
                                        {revealSecondsLeft}s)
                                      </>
                                    ) : (
                                      <>
                                        <Eye
                                          className="h-3.5 w-3.5"
                                          aria-hidden="true"
                                        />
                                        {t('employees.pii.revealButton')}
                                      </>
                                    )}
                                  </Button>
                                )}
                              </div>

                              <p className="text-xs text-slate-500">
                                {t('employees.pii.description')}
                              </p>

                              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.nieDni')}
                                  </Label>
                                  <div className="font-mono text-sm font-medium text-slate-900 tabular-nums">
                                    {decryptedPii?.nie_dni ??
                                      employee.nie_dni_masked ??
                                      '—'}
                                  </div>
                                  {isEditing && (
                                    <Input
                                      value={newNieDni}
                                      onChange={(e) =>
                                        setNewNieDni(e.target.value)
                                      }
                                      placeholder={t(
                                        'employees.pii.rotatePlaceholder',
                                      )}
                                      className="mt-1 bg-white font-mono uppercase tabular-nums"
                                    />
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {t('employees.fields.naf')}
                                  </Label>
                                  <div className="font-mono text-sm font-medium text-slate-900 tabular-nums">
                                    {decryptedPii?.naf ??
                                      employee.naf_masked ??
                                      '—'}
                                  </div>
                                  {isEditing && (
                                    <Input
                                      value={newNaf}
                                      onChange={(e) =>
                                        setNewNaf(e.target.value)
                                      }
                                      placeholder={t(
                                        'employees.pii.rotatePlaceholder',
                                      )}
                                      className="mt-1 bg-white font-mono tabular-nums"
                                    />
                                  )}
                                </div>
                              </div>

                              {/* Payslips (Nóminas) */}
                              <div className="space-y-2 border-t border-slate-200/75 pt-3">
                                <div className="text-xs font-semibold text-slate-700">
                                  {isEs
                                    ? 'Nóminas (Recibos de salario)'
                                    : '工资单 (Nóminas)'}
                                </div>
                                {payslipsError && (
                                  <div
                                    role="alert"
                                    className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
                                  >
                                    {payslipsError}
                                  </div>
                                )}
                                {payslipsLoading ? (
                                  <Skeleton className="h-24 w-full rounded-lg" />
                                ) : payslips.length === 0 ? (
                                  <EmptyState
                                    icon={FileSpreadsheet}
                                    title={
                                      isEs
                                        ? 'Sin nóminas cargadas'
                                        : '暂无已上传的工资单'
                                    }
                                    description={
                                      isEs
                                        ? 'Las nóminas mensuales subidas aparecerán aquí con descarga segura de 60 segundos.'
                                        : '上传的月度工资单（Nómina）将显示在此处，支持 60 秒加密安全下载。'
                                    }
                                    className="border-dashed bg-white shadow-none"
                                  />
                                ) : (
                                  <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                                    {payslips.map((item) => (
                                      <li
                                        key={item.document.id}
                                        className="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5"
                                      >
                                        <div className="min-w-0">
                                          <div className="truncate text-sm font-medium text-slate-900">
                                            {item.document.title}
                                          </div>
                                          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 tabular-nums">
                                            {item.document.period_month && (
                                              <span>
                                                {item.document.period_month}
                                              </span>
                                            )}
                                            <span>
                                              {formatUtcDateTime(
                                                item.document.created_at,
                                                locale,
                                              )}
                                            </span>
                                          </div>
                                        </div>
                                        <div className="flex shrink-0 items-center gap-2">
                                          <Badge
                                            variant="outline"
                                            className="border-sky-200 bg-sky-50 text-xs text-sky-700"
                                          >
                                            Nómina
                                          </Badge>
                                          <Button
                                            type="button"
                                            size="sm"
                                            variant="outline"
                                            disabled={
                                              downloadingPayslipId ===
                                              item.document.id
                                            }
                                            onClick={() =>
                                              void handleDownloadPayslip(
                                                item.document.id,
                                              )
                                            }
                                            className="h-8 text-xs"
                                          >
                                            <Download
                                              className="h-3.5 w-3.5"
                                              aria-hidden="true"
                                            />
                                            {t(
                                              'documents.detailDrawer.downloadButton',
                                            )}
                                          </Button>
                                        </div>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            </section>

                            {/* Section 3: Bank Account (IBAN) */}
                            <section className="space-y-3 rounded-xl border border-slate-200/80 bg-slate-50/40 p-4">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <h3 className="flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                                  <CreditCard
                                    className="h-3.5 w-3.5 text-[#2563EB]"
                                    aria-hidden="true"
                                  />
                                  {t('employees.profile.hrSections.bank')}
                                </h3>
                                {canDecryptPii && (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="outline"
                                    disabled={revealing}
                                    onClick={() => {
                                      if (decryptedPii) {
                                        setDecryptedPii(null)
                                        setRevealSecondsLeft(0)
                                      } else {
                                        void handleRevealPii()
                                      }
                                    }}
                                    className="h-7 bg-white text-xs tabular-nums"
                                  >
                                    {decryptedPii ? (
                                      <>
                                        <EyeOff
                                          className="h-3.5 w-3.5"
                                          aria-hidden="true"
                                        />
                                        {t('employees.pii.hideButton')} (
                                        {revealSecondsLeft}s)
                                      </>
                                    ) : (
                                      <>
                                        <Eye
                                          className="h-3.5 w-3.5"
                                          aria-hidden="true"
                                        />
                                        {t('employees.pii.revealButton')}
                                      </>
                                    )}
                                  </Button>
                                )}
                              </div>

                              <div className="space-y-1.5">
                                <Label className="text-xs text-slate-500">
                                  {t('employees.fields.iban')}
                                </Label>
                                <div className="font-mono text-sm font-medium text-slate-900 tabular-nums">
                                  {decryptedPii?.iban ??
                                    employee.iban_masked ??
                                    '—'}
                                </div>
                                {isEditing && (
                                  <Input
                                    value={newIban}
                                    onChange={(e) => setNewIban(e.target.value)}
                                    placeholder={t(
                                      'employees.pii.rotatePlaceholder',
                                    )}
                                    className="mt-1 bg-white font-mono uppercase tabular-nums"
                                  />
                                )}
                              </div>
                            </section>

                            {/* Section 4: Emergency Contact */}
                            <section className="rounded-xl border border-slate-200/80 bg-slate-50/40 p-4">
                              <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-700 uppercase">
                                <PhoneCall
                                  className="h-3.5 w-3.5 text-[#2563EB]"
                                  aria-hidden="true"
                                />
                                {t('employees.profile.hrSections.emergency')}
                              </h3>
                              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {isEs
                                      ? 'Nombre de contacto'
                                      : '紧急联系人姓名'}
                                  </Label>
                                  {isEditing ? (
                                    <Input
                                      className="bg-white"
                                      value={emergencyContactName}
                                      onChange={(e) =>
                                        setEmergencyContactName(e.target.value)
                                      }
                                    />
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900">
                                      {employee.emergency_contact_name || '—'}
                                    </div>
                                  )}
                                </div>

                                <div className="space-y-1.5">
                                  <Label className="text-xs text-slate-500">
                                    {isEs
                                      ? 'Teléfono de emergencia'
                                      : '紧急联系电话'}
                                  </Label>
                                  {isEditing ? (
                                    <Input
                                      className="bg-white tabular-nums"
                                      value={emergencyContactPhone}
                                      onChange={(e) =>
                                        setEmergencyContactPhone(e.target.value)
                                      }
                                    />
                                  ) : (
                                    <div className="text-sm font-medium text-slate-900 tabular-nums">
                                      {employee.emergency_contact_phone || '—'}
                                    </div>
                                  )}
                                </div>
                              </div>
                            </section>
                          </div>
                        )}
                      </CardContent>

                      {/* Sticky Bottom Modify / Save Bar */}
                      {canManage && isEditing && (
                        <div className="sticky bottom-0 flex items-center justify-end gap-2 rounded-b-xl border-t border-slate-200 bg-slate-50/95 px-6 py-3 backdrop-blur-xs">
                          <Button
                            type="button"
                            variant="outline"
                            size="sm"
                            onClick={() => {
                              syncForm(employee)
                              setIsEditing(false)
                            }}
                          >
                            {t('common.cancel')}
                          </Button>
                          <Button
                            type="submit"
                            size="sm"
                            disabled={saving}
                            className="bg-[#2563EB] text-white hover:bg-blue-700"
                          >
                            {saving
                              ? t('employees.drawer.saving')
                              : isEs
                                ? 'Guardar'
                                : '保存'}
                          </Button>
                        </div>
                      )}
                    </Card>
                  </form>

                  {/* Lifecycle & 4-Year Retention Compliance Controls */}
                  {canManageLifecycle && (
                    <Card className="gap-3 py-4">
                      <CardHeader className="px-6 pb-0">
                        <CardTitle className="flex items-center gap-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                          <ShieldAlert
                            className="h-3.5 w-3.5 text-slate-400"
                            aria-hidden="true"
                          />
                          {t('employees.lifecycle.title')}
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="flex flex-wrap items-center justify-between gap-3 px-6 text-xs text-slate-600">
                        <div className="tabular-nums">
                          {employee.retention_until
                            ? `${t('employees.lifecycle.retentionUntil')}: ${formatUtcDateTime(employee.retention_until, locale)}`
                            : t('employees.lifecycle.activeRetentionHint')}
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          {employee.employment_status !== 'terminated' &&
                            employee.employment_status !== 'anonymized' && (
                              <Button
                                type="button"
                                size="xs"
                                variant="outline"
                                onClick={() => setTerminateDialogOpen(true)}
                                className="border-rose-200 text-rose-700 hover:bg-rose-50"
                              >
                                {t('employees.lifecycle.terminateBtn')}
                              </Button>
                            )}
                          {employee.employment_status === 'terminated' && (
                            <Button
                              type="button"
                              size="xs"
                              variant="outline"
                              disabled={!retentionExpired}
                              onClick={() => setAnonymizeDialogOpen(true)}
                            >
                              {t('employees.lifecycle.anonymizeBtn')}
                            </Button>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  )}
                </div>
              </div>
            )}

            {/* Avatar Cropper Dialog */}
            <AvatarCropperDialog
              open={avatarDialogOpen}
              onOpenChange={setAvatarDialogOpen}
              employeeId={employee.id}
              hasExistingAvatar={Boolean(employee.avatar_url)}
              onSaved={() => void loadEmployee()}
            />

            {/* Terminate Dialog */}
            <AlertDialog
              open={terminateDialogOpen}
              onOpenChange={setTerminateDialogOpen}
            >
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    {t('employees.lifecycle.terminateConfirmTitle')}
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('employees.lifecycle.terminateConfirmDesc')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <div className="py-2">
                  <Label htmlFor="term-date">
                    {t('employees.lifecycle.terminationDate')}
                  </Label>
                  <Input
                    id="term-date"
                    type="date"
                    className="mt-1 tabular-nums"
                    value={terminationDate}
                    onChange={(e) => setTerminationDate(e.target.value)}
                  />
                </div>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => void handleTerminateConfirm()}
                    className="bg-rose-600 text-white hover:bg-rose-700"
                  >
                    {t('employees.lifecycle.terminateBtn')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>

            {/* Anonymize Dialog */}
            <AlertDialog
              open={anonymizeDialogOpen}
              onOpenChange={setAnonymizeDialogOpen}
            >
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    {t('employees.lifecycle.anonymizeConfirmTitle')}
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('employees.lifecycle.anonymizeConfirmDesc')}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={() => void handleAnonymizeConfirm()}
                    className="bg-rose-600 text-white hover:bg-rose-700"
                  >
                    {t('employees.lifecycle.anonymizeBtn')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </>
        )
      )}
    </div>
  )
}
