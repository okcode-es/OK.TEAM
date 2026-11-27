import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ErrorBoundary } from '@/components/common/ErrorBoundary'
import { AppShell, type NavSection } from '@/components/layout/AppShell'
import { MobileAppsHub } from '@/components/layout/MobileAppsHub'
import { Skeleton } from '@/components/ui/skeleton'
import { AnnouncementsPage } from '@/features/announcements/AnnouncementsPage'
import { AuthProvider, useAuth } from '@/features/auth/AuthContext'
import { LoginPage } from '@/features/auth/LoginPage'
import { OnboardingPage } from '@/features/auth/OnboardingPage'
import {
  canRoleOperateKiosk,
  KIOSK_LOCK_STORAGE_KEY,
  readKioskLockForSession,
} from '@/features/attendance/kioskLock'
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
  const { isLoading, isAuthenticated, profile, activeTenant } = useAuth()
  const [section, setSection] = useState<NavSection>('dashboard')
  const [viewedEmployeeId, setViewedEmployeeId] = useState<string | null>(null)
  const [kioskLockVersion, setKioskLockVersion] = useState(0)

  const canOperateKiosk = canRoleOperateKiosk(activeTenant?.role)
  const lockedKioskRecord = useMemo(() => {
    if (kioskLockVersion < 0) return null
    return readKioskLockForSession(
      activeTenant?.tenant_id,
      profile?.user_id,
      activeTenant?.role,
    )
  }, [activeTenant?.tenant_id, activeTenant?.role, profile?.user_id, kioskLockVersion])
  const isKioskLocked = lockedKioskRecord !== null
  const effectiveSection: NavSection = isKioskLocked ? 'kiosk' : section

  const handleNavigate = (next: NavSection) => {
    if (isKioskLocked && next !== 'kiosk') {
      return
    }
    setViewedEmployeeId(null)
    setSection(next)
  }

  const handleKioskLockStateChange = () => {
    setKioskLockVersion((v) => v + 1)
  }

  const handleKioskExit = () => {
    setKioskLockVersion((v) => v + 1)
    setViewedEmployeeId(null)
    setSection('dashboard')
  }

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === KIOSK_LOCK_STORAGE_KEY || e.key === null) {
        setKioskLockVersion((v) => v + 1)
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  useEffect(() => {
    document.documentElement.lang = i18n.language || 'zh-CN'
  }, [i18n.language])

  useEffect(() => {
    setViewedEmployeeId(null)
  }, [activeTenant?.tenant_id])

  useEffect(() => {
    if (isKioskLocked) {
      if (section !== 'kiosk') {
        setSection('kiosk')
      }
      return
    }
    if (
      activeTenant?.role === 'employee' &&
      EMPLOYEE_FORBIDDEN_SECTIONS.has(section)
    ) {
      setSection('dashboard')
    } else if (section === 'kiosk' && !canOperateKiosk) {
      setSection('dashboard')
    } else if (
      section === 'organization' &&
      activeTenant?.role !== 'owner'
    ) {
      setSection('dashboard')
    }
  }, [activeTenant?.role, canOperateKiosk, isKioskLocked, section])

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

  if (effectiveSection === 'kiosk' && canOperateKiosk) {
    return (
      <KioskPage
        key={`${activeTenant.tenant_id}:${profile?.user_id ?? ''}`}
        lockedRecord={lockedKioskRecord}
        onLockStateChange={handleKioskLockStateChange}
        onExit={handleKioskExit}
      />
    )
  }

  return (
    <AppShell activeSection={effectiveSection} onNavigate={handleNavigate}>
      {effectiveSection === 'dashboard' &&
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
      {effectiveSection === 'employees' && <EmployeesPage />}
      {effectiveSection === 'attendance' && (
        <LeavesPage key="time-attendance" initialTab="attendance" />
      )}
      {effectiveSection === 'schedule' && <SchedulePage />}
      {effectiveSection === 'leaves' && (
        <LeavesPage key="time-leaves" initialTab="team" />
      )}
      {effectiveSection === 'documents' && <DocumentsPage />}
      {effectiveSection === 'announcements' && <AnnouncementsPage />}
      {effectiveSection === 'payroll' && activeTenant.role !== 'employee' && (
        <PayrollPage onNavigate={handleNavigate} />
      )}
      {effectiveSection === 'reports' && activeTenant.role !== 'employee' && (
        <ReportsPage />
      )}
      {effectiveSection === 'stores' && activeTenant.role !== 'employee' && (
        <StoresPage />
      )}
      {effectiveSection === 'members' && activeTenant.role !== 'employee' && (
        <MembersPage />
      )}
      {effectiveSection === 'organization' && activeTenant.role === 'owner' && (
        <OrganizationSettingsPage />
      )}
      {effectiveSection === 'apps' && (
        <MobileAppsHub onNavigate={handleNavigate} />
      )}
      {effectiveSection === 'profile' && (
        <ProfileRouteView onNavigate={handleNavigate} />
      )}
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
