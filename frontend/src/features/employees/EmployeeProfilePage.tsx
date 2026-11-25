import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ArrowLeft, Eye, EyeOff, Lock, ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { apiClient, extractApiError } from '@/api/client'
import type {
  ContractType,
  EmployeeResponse,
  EmployeeSensitivePiiResponse,
  EmploymentStatus,
  StoreResponse,
  WorkScheduleType,
} from '@/api/types'
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
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
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
import { formatUtcDateTime, isImeComposing } from '@/lib/ime'

const REVEAL_DURATION_SECONDS = 15

interface EmployeeProfilePageProps {
  employeeId: string
  stores: StoreResponse[]
  onBack: () => void
}

export function EmployeeProfilePage({
  employeeId,
  stores,
  onBack,
}: EmployeeProfilePageProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant, profile } = useAuth()
  const role = activeTenant?.role

  const [employee, setEmployee] = useState<EmployeeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [savedNotice, setSavedNotice] = useState(false)

  // Editable fields
  const [primaryStoreId, setPrimaryStoreId] = useState('')
  const [employeeCode, setEmployeeCode] = useState('')
  const [fullName, setFullName] = useState('')
  const [preferredName, setPreferredName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [birthDate, setBirthDate] = useState('')
  const [hireDate, setHireDate] = useState('')
  const [contractType, setContractType] = useState<ContractType>('indefinido')
  const [scheduleType, setScheduleType] =
    useState<WorkScheduleType>('full_time')
  const [weeklyHours, setWeeklyHours] = useState('40.00')
  const [vacationDays, setVacationDays] = useState('30')
  const [empStatus, setEmpStatus] = useState<EmploymentStatus>('active')

  // Optional PII rotation inputs (never prefilled with plaintext)
  const [newNieDni, setNewNieDni] = useState('')
  const [newIban, setNewIban] = useState('')
  const [newNaf, setNewNaf] = useState('')

  // On-demand 15-second ephemeral plaintext PII viewer (in-memory only)
  const [decryptedPii, setDecryptedPii] =
    useState<EmployeeSensitivePiiResponse | null>(null)
  const [revealSecondsLeft, setRevealSecondsLeft] = useState(0)
  const [revealing, setRevealing] = useState(false)

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
    setEmail(data.email)
    setPhone(data.phone)
    setBirthDate(data.birth_date ?? '')
    setHireDate(data.hire_date)
    setContractType(data.contract_type)
    setScheduleType(data.work_schedule_type)
    setWeeklyHours(String(data.contracted_weekly_hours))
    setVacationDays(String(data.annual_vacation_days))
    setEmpStatus(data.employment_status)
    setNewNieDni('')
    setNewIban('')
    setNewNaf('')
  }

  const loadEmployee = useCallback(async () => {
    setLoading(true)
    setErrorBanner(null)
    try {
      const { data, error } = await apiClient.GET(
        '/api/v1/employees/{employee_id}',
        {
          params: { path: { employee_id: employeeId } },
        },
      )
      if (error || !data) {
        setErrorBanner(extractApiError(error, t('common.error')))
        return
      }
      setEmployee(data)
      syncForm(data)
    } finally {
      setLoading(false)
    }
  }, [employeeId, t])

  useEffect(() => {
    void loadEmployee()
  }, [loadEmployee])

  // Auto-mask decrypted PII after 15 seconds
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button type="button" variant="outline" size="sm" onClick={onBack}>
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            {t('employees.profile.backToList')}
          </Button>
          {employee && (
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-lg font-semibold text-slate-900">
                {employee.full_name}
              </h1>
              <span className="font-mono text-xs text-slate-500 tabular-nums">
                {employee.employee_code}
              </span>
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-700"
              >
                {t(`employees.employmentStatuses.${employee.employment_status}`)}
              </Badge>
            </div>
          )}
        </div>
      </div>

      {isFrozen && (
        <div className="flex items-center gap-2 rounded-md border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-xs text-rose-700">
          <Lock className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{t('employees.profile.frozenBanner')}</span>
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

      {savedNotice && (
        <div
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-xs text-emerald-700"
        >
          {t('employees.profile.savedNotice')}
        </div>
      )}

      {/* Template 2: Two-Column Profile Configuration Layout */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <aside className="lg:col-span-3">
          <nav className="flex flex-row flex-wrap gap-1 lg:flex-col">
            <a
              href="#emp-basic"
              className="rounded-md bg-white px-3 py-2 text-sm font-medium text-slate-900 ring-1 ring-slate-200"
            >
              {t('employees.profile.navBasic')}
            </a>
            <a
              href="#emp-contract"
              className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-slate-900"
            >
              {t('employees.profile.navContract')}
            </a>
            <a
              href="#emp-pii"
              className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-slate-900"
            >
              {t('employees.profile.navPii')}
            </a>
            <a
              href="#emp-retention"
              className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-white hover:text-slate-900"
            >
              {t('employees.profile.navRetention')}
            </a>
          </nav>
        </aside>

        <div className="space-y-4 lg:col-span-9">
          {loading || !employee ? (
            <Card className="border-slate-200 bg-white shadow-none">
              <CardContent className="space-y-4 p-6">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
              </CardContent>
            </Card>
          ) : (
            <>
              <form
                onSubmit={handleSave}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && isImeComposing(e)) {
                    e.preventDefault()
                  }
                }}
                className="space-y-4"
              >
                <Card
                  id="emp-basic"
                  className="scroll-mt-20 border-slate-200 bg-white shadow-none"
                >
                  <CardHeader>
                    <CardTitle className="text-base font-semibold">
                      {t('employees.profile.navBasic')}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-code">
                        {t('employees.fields.code')}
                      </Label>
                      <Input
                        id="prof-code"
                        disabled={!canManage}
                        className="uppercase tabular-nums"
                        value={employeeCode}
                        onChange={(e) => setEmployeeCode(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-store">
                        {t('employees.fields.primaryStore')}
                      </Label>
                      <Select
                        disabled={!canManage}
                        value={primaryStoreId}
                        onValueChange={setPrimaryStoreId}
                      >
                        <SelectTrigger id="prof-store" className="w-full">
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
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-name">
                        {t('employees.fields.fullName')}
                      </Label>
                      <Input
                        id="prof-name"
                        disabled={!canManage}
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-pref">
                        {t('employees.fields.preferredName')}
                      </Label>
                      <Input
                        id="prof-pref"
                        disabled={!canManage}
                        value={preferredName}
                        onChange={(e) => setPreferredName(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-email">
                        {t('employees.fields.email')}
                      </Label>
                      <Input
                        id="prof-email"
                        type="email"
                        disabled={!canManage}
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-phone">
                        {t('employees.fields.phone')}
                      </Label>
                      <Input
                        id="prof-phone"
                        disabled={!canManage}
                        className="tabular-nums"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-birth">
                        {t('employees.fields.birthDate')}
                      </Label>
                      <Input
                        id="prof-birth"
                        type="date"
                        disabled={!canManage}
                        className="tabular-nums"
                        value={birthDate}
                        onChange={(e) => setBirthDate(e.target.value)}
                      />
                    </div>
                  </CardContent>
                </Card>

                <Card
                  id="emp-contract"
                  className="scroll-mt-20 border-slate-200 bg-white shadow-none"
                >
                  <CardHeader>
                    <CardTitle className="text-base font-semibold">
                      {t('employees.profile.navContract')}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-2">
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-contract">
                        {t('employees.fields.contractType')}
                      </Label>
                      <Select
                        disabled={!canManage}
                        value={contractType}
                        onValueChange={(v) =>
                          setContractType(v as ContractType)
                        }
                      >
                        <SelectTrigger id="prof-contract" className="w-full">
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
                      <Label htmlFor="prof-sched">
                        {t('employees.fields.scheduleType')}
                      </Label>
                      <Select
                        disabled={!canManage}
                        value={scheduleType}
                        onValueChange={(v) =>
                          setScheduleType(v as WorkScheduleType)
                        }
                      >
                        <SelectTrigger id="prof-sched" className="w-full">
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
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-hours">
                        {t('employees.fields.weeklyHours')}
                      </Label>
                      <Input
                        id="prof-hours"
                        type="number"
                        step="0.5"
                        min="1"
                        max="40"
                        disabled={!canManage}
                        className="tabular-nums"
                        value={weeklyHours}
                        onChange={(e) => setWeeklyHours(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-vac">
                        {t('employees.fields.vacationDays')}
                      </Label>
                      <Input
                        id="prof-vac"
                        type="number"
                        min="30"
                        max="90"
                        disabled={!canManage}
                        className="tabular-nums"
                        value={vacationDays}
                        onChange={(e) => setVacationDays(e.target.value)}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="prof-hire">
                        {t('employees.fields.hireDate')}
                      </Label>
                      <Input
                        id="prof-hire"
                        type="date"
                        disabled={!canManage}
                        className="tabular-nums"
                        value={hireDate}
                        onChange={(e) => setHireDate(e.target.value)}
                      />
                    </div>
                  </CardContent>
                </Card>

                {/* Encrypted PII Card */}
                <Card
                  id="emp-pii"
                  className="scroll-mt-20 border-slate-200 bg-white shadow-none"
                >
                  <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
                    <div className="space-y-1">
                      <CardTitle className="text-base font-semibold">
                        {t('employees.profile.navPii')}
                      </CardTitle>
                      <CardDescription>
                        {t('employees.profile.piiSubtitle')}
                      </CardDescription>
                    </div>

                    {canDecryptPii &&
                      employee.employment_status !== 'anonymized' &&
                      (decryptedPii ? (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          className="tabular-nums"
                          onClick={() => {
                            setDecryptedPii(null)
                            setRevealSecondsLeft(0)
                          }}
                        >
                          <EyeOff className="h-4 w-4" aria-hidden="true" />
                          {t('employees.profile.hidePii', {
                            seconds: revealSecondsLeft,
                          })}
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={revealing}
                          onClick={() => void handleRevealPii()}
                        >
                          <Eye className="h-4 w-4" aria-hidden="true" />
                          {revealing
                            ? t('employees.profile.revealing')
                            : t('employees.profile.revealPii')}
                        </Button>
                      ))}
                  </CardHeader>

                  <CardContent className="space-y-4">
                    {role === 'store_manager' && (
                      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                        {t('employees.profile.storeManagerNoPii')}
                      </div>
                    )}

                    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs text-slate-500">NIE / DNI</p>
                        <p className="mt-1 font-mono text-sm font-medium text-slate-900 tabular-nums">
                          {decryptedPii
                            ? (decryptedPii.nie_dni ??
                              t('employees.profile.notSet'))
                            : (employee.nie_dni_masked ??
                              t('employees.profile.notSet'))}
                        </p>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs text-slate-500">IBAN</p>
                        <p className="mt-1 font-mono text-sm font-medium break-all text-slate-900 tabular-nums">
                          {decryptedPii
                            ? (decryptedPii.iban ??
                              t('employees.profile.notSet'))
                            : (employee.iban_masked ??
                              t('employees.profile.notSet'))}
                        </p>
                      </div>
                      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                        <p className="text-xs text-slate-500">NAF</p>
                        <p className="mt-1 font-mono text-sm font-medium text-slate-900 tabular-nums">
                          {decryptedPii
                            ? (decryptedPii.naf ??
                              t('employees.profile.notSet'))
                            : (employee.naf_masked ??
                              t('employees.profile.notSet'))}
                        </p>
                      </div>
                    </div>

                    {canManage && (
                      <div className="space-y-3 border-t border-slate-200 pt-4">
                        <p className="text-xs text-slate-500">
                          {t('employees.profile.piiRotateHint')}
                        </p>
                        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                          <div className="space-y-1.5">
                            <Label htmlFor="rot-nie">NIE / DNI</Label>
                            <Input
                              id="rot-nie"
                              spellCheck={false}
                              autoComplete="off"
                              className="font-mono uppercase tabular-nums"
                              value={newNieDni}
                              onChange={(e) => setNewNieDni(e.target.value)}
                              placeholder="Y1234567X"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label htmlFor="rot-iban">IBAN</Label>
                            <Input
                              id="rot-iban"
                              spellCheck={false}
                              autoComplete="off"
                              className="font-mono uppercase tabular-nums"
                              value={newIban}
                              onChange={(e) => setNewIban(e.target.value)}
                              placeholder="ES91…"
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label htmlFor="rot-naf">NAF</Label>
                            <Input
                              id="rot-naf"
                              spellCheck={false}
                              autoComplete="off"
                              className="font-mono tabular-nums"
                              value={newNaf}
                              onChange={(e) => setNewNaf(e.target.value)}
                              placeholder="281234567840"
                            />
                          </div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {canManage && (
                  <div className="flex justify-end">
                    <Button type="submit" disabled={saving}>
                      {saving
                        ? t('organization.saving')
                        : t('organization.save')}
                    </Button>
                  </div>
                )}
              </form>

              {/* 4-Year Legal Hold & GDPR Anonymization Card */}
              <Card
                id="emp-retention"
                className="scroll-mt-20 border-slate-200 bg-white shadow-none"
              >
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base font-semibold">
                    <ShieldAlert
                      className="h-4 w-4 text-slate-600"
                      aria-hidden="true"
                    />
                    {t('employees.profile.navRetention')}
                  </CardTitle>
                  <CardDescription>
                    {t('employees.profile.retentionSubtitle')}
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                      <p className="text-xs text-slate-500">
                        {t('employees.profile.terminationDate')}
                      </p>
                      <p className="mt-1 text-sm font-medium text-slate-900 tabular-nums">
                        {employee.termination_date ?? '—'}
                      </p>
                    </div>
                    <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                      <p className="text-xs text-slate-500">
                        {t('employees.profile.retentionUntil')}
                      </p>
                      <p className="mt-1 text-sm font-medium text-slate-900 tabular-nums">
                        {employee.retention_until
                          ? formatUtcDateTime(
                              employee.retention_until,
                              i18n.language,
                              'UTC',
                            )
                          : '—'}
                      </p>
                    </div>
                  </div>

                  {canManageLifecycle &&
                    employee.employment_status !== 'anonymized' && (
                      <div className="flex flex-wrap items-end justify-between gap-3 border-t border-slate-200 pt-4">
                        {!isFrozen ? (
                          <div className="flex flex-wrap items-end gap-2.5">
                            <div className="space-y-1.5">
                              <Label htmlFor="term-date">
                                {t('employees.profile.terminationDate')}
                              </Label>
                              <Input
                                id="term-date"
                                type="date"
                                className="tabular-nums"
                                value={terminationDate}
                                onChange={(e) =>
                                  setTerminationDate(e.target.value)
                                }
                              />
                            </div>
                            <Button
                              type="button"
                              variant="outline"
                              className="border-rose-200 text-rose-700 hover:bg-rose-50 hover:text-rose-800"
                              onClick={() => setTerminateDialogOpen(true)}
                            >
                              {t('employees.profile.terminateAction')}
                            </Button>
                          </div>
                        ) : (
                          <div className="space-y-1">
                            <Button
                              type="button"
                              variant="outline"
                              disabled={!retentionExpired}
                              className="border-rose-200 text-rose-700 hover:bg-rose-50"
                              onClick={() => setAnonymizeDialogOpen(true)}
                            >
                              {t('employees.profile.anonymizeAction')}
                            </Button>
                            {!retentionExpired && (
                              <p className="text-xs text-slate-500">
                                {t('employees.profile.anonymizeLockedHint')}
                              </p>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </div>

      <AlertDialog
        open={terminateDialogOpen}
        onOpenChange={setTerminateDialogOpen}
      >
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('employees.profile.terminateDialog.title')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('employees.profile.terminateDialog.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('employees.profile.terminateDialog.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-rose-700 text-white hover:bg-rose-800"
              onClick={() => void handleTerminateConfirm()}
            >
              {t('employees.profile.terminateDialog.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog
        open={anonymizeDialogOpen}
        onOpenChange={setAnonymizeDialogOpen}
      >
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('employees.profile.anonymizeDialog.title')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('employees.profile.anonymizeDialog.description')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {t('employees.profile.anonymizeDialog.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-rose-700 text-white hover:bg-rose-800"
              onClick={() => void handleAnonymizeConfirm()}
            >
              {t('employees.profile.anonymizeDialog.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
