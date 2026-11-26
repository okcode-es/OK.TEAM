import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ErrorBoundary } from '@/components/common/ErrorBoundary'
import { AppShell, type NavSection } from '@/components/layout/AppShell'
import { MobileAppsHub } from '@/components/layout/MobileAppsHub'
import { Skeleton } from '@/components/ui/skeleton'
import { AnnouncementsPage } from '@/features/announcements/AnnouncementsPage'
import { AuthProvider, useAuth } from '@/features/auth/AuthContext'
import { LoginPage } from '@/features/auth/LoginPage'
import { OnboardingPage } from '@/features/auth/OnboardingPage'
import { AttendancePage } from '@/features/attendance/AttendancePage'
import { KioskPage } from '@/features/attendance/KioskPage'
import { ClockStateProvider, useClockState } from '@/features/clock/ClockStateContext'
import { DashboardPage } from '@/features/dashboard/DashboardPage'
import { DocumentsPage } from '@/features/documents/DocumentsPage'
import { EmployeeProfilePage } from '@/features/employees/EmployeeProfilePage'
import { EmployeesPage } from '@/features/employees/EmployeesPage'
import { LeavesPage } from '@/features/leaves/LeavesPage'
import { MembersPage } from '@/features/members/MembersPage'
import { OrganizationSettingsPage } from '@/features/organization/OrganizationSettingsPage'
import { PayrollPage } from '@/features/payroll/PayrollPage'
import { ReportsPage } from '@/features/reports/ReportsPage'
import { SchedulePage } from '@/features/schedule/SchedulePage'
import { StoresPage } from '@/features/stores/StoresPage'

function ProfileRouteView({
  onNavigate,
}: {
  onNavigate: (section: NavSection) => void
}) {
  const { t } = useTranslation()
  const { profile, activeTenant, signOut } = useAuth()
  const { hasEmployeeProfile, employee } = useClockState()

  if (hasEmployeeProfile && employee) {
    return (
      <EmployeeProfilePage
        employeeId={employee.id}
        onBack={() => onNavigate('dashboard')}
        backLabel={t('nav.dashboard')}
      />
    )
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200/75 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
        <div className="text-base font-semibold text-slate-900">
          {profile?.email ?? ''}
        </div>
        <div className="mt-1 text-xs text-slate-500">
          {activeTenant?.tenant_trade_name} ·{' '}
          {activeTenant ? t(`roles.${activeTenant.role}`) : ''}
        </div>
        <p className="mt-3 text-xs text-slate-600">
          {t('dashboard.noEmployeeProfileDesc')}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onNavigate('documents')}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700"
        >
          {t('nav.documents')}
        </button>
        <button
          type="button"
          onClick={() => onNavigate('leaves')}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700"
        >
          {t('nav.leaves')}
        </button>
        <button
          type="button"
          onClick={() => void signOut()}
          className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-medium text-rose-700"
        >
          {t('nav.signOut')}
        </button>
      </div>
    </div>
  )
}

const EMPLOYEE_FORBIDDEN_SECTIONS: ReadonlySet<NavSection> = new Set([
  'payroll',
  'reports',
  'kiosk',
  'stores',
  'members',
  'organization',
])

function WorkspaceRouter() {
  const { t, i18n } = useTranslation()
  const { isLoading, isAuthenticated, activeTenant } = useAuth()
  const [section, setSection] = useState<NavSection>('dashboard')
  const [viewedEmployeeId, setViewedEmployeeId] = useState<string | null>(null)

  const handleNavigate = (next: NavSection) => {
    setViewedEmployeeId(null)
    setSection(next)
  }

  useEffect(() => {
    document.documentElement.lang = i18n.language || 'zh-CN'
  }, [i18n.language])

  useEffect(() => {
    setViewedEmployeeId(null)
  }, [activeTenant?.tenant_id])

  useEffect(() => {
    if (
      activeTenant?.role === 'employee' &&
      EMPLOYEE_FORBIDDEN_SECTIONS.has(section)
    ) {
      setSection('dashboard')
    } else if (
      section === 'organization' &&
      activeTenant?.role !== 'owner'
    ) {
      setSection('dashboard')
    }
  }, [activeTenant?.role, section])

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
        <div className="w-full max-w-md space-y-3 rounded-md border border-slate-200 bg-white p-6">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    return <LoginPage />
  }

  if (!activeTenant) {
    return <OnboardingPage />
  }

  return (
    <AppShell activeSection={section} onNavigate={handleNavigate}>
      {section === 'dashboard' &&
        (viewedEmployeeId ? (
          <EmployeeProfilePage
            employeeId={viewedEmployeeId}
            onBack={() => setViewedEmployeeId(null)}
            backLabel={t('nav.dashboard')}
          />
        ) : (
          <DashboardPage
            onNavigate={handleNavigate}
            onOpenEmployeeProfile={(empId) => setViewedEmployeeId(empId)}
          />
        ))}
      {section === 'employees' && <EmployeesPage />}
      {section === 'attendance' && <AttendancePage />}
      {section === 'kiosk' && activeTenant.role !== 'employee' && <KioskPage />}
      {section === 'schedule' && <SchedulePage />}
      {section === 'leaves' && <LeavesPage />}
      {section === 'documents' && <DocumentsPage />}
      {section === 'announcements' && <AnnouncementsPage />}
      {section === 'payroll' && activeTenant.role !== 'employee' && (
        <PayrollPage onNavigate={handleNavigate} />
      )}
      {section === 'reports' && activeTenant.role !== 'employee' && (
        <ReportsPage />
      )}
      {section === 'stores' && activeTenant.role !== 'employee' && <StoresPage />}
      {section === 'members' && activeTenant.role !== 'employee' && (
        <MembersPage />
      )}
      {section === 'organization' && activeTenant.role === 'owner' && (
        <OrganizationSettingsPage />
      )}
      {section === 'apps' && <MobileAppsHub onNavigate={handleNavigate} />}
      {section === 'profile' && <ProfileRouteView onNavigate={handleNavigate} />}
    </AppShell>
  )
}

export function App() {
  return (
    <ErrorBoundary>
      <AuthProvider>
        <ClockStateProvider>
          <WorkspaceRouter />
        </ClockStateProvider>
      </AuthProvider>
    </ErrorBoundary>
  )
}
