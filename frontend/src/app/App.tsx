import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AppShell, type NavSection } from '@/components/layout/AppShell'
import { Skeleton } from '@/components/ui/skeleton'
import { AuthProvider, useAuth } from '@/features/auth/AuthContext'
import { LoginPage } from '@/features/auth/LoginPage'
import { OnboardingPage } from '@/features/auth/OnboardingPage'
import { EmployeesPage } from '@/features/employees/EmployeesPage'
import { AttendancePage } from '@/features/attendance/AttendancePage'
import { KioskPage } from '@/features/attendance/KioskPage'
import { MembersPage } from '@/features/members/MembersPage'
import { OrganizationSettingsPage } from '@/features/organization/OrganizationSettingsPage'
import { StoresPage } from '@/features/stores/StoresPage'

function WorkspaceRouter() {
  const { i18n } = useTranslation()
  const { isLoading, isAuthenticated, activeTenant } = useAuth()
  const [section, setSection] = useState<NavSection>('employees')

  useEffect(() => {
    document.documentElement.lang = i18n.language || 'zh-CN'
  }, [i18n.language])

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
    <AppShell activeSection={section} onNavigate={setSection}>
      {section === 'employees' && <EmployeesPage />}
      {section === 'attendance' && <AttendancePage />}
      {section === 'kiosk' && <KioskPage />}
      {section === 'stores' && <StoresPage />}
      {section === 'members' && <MembersPage />}
      {section === 'organization' && <OrganizationSettingsPage />}
    </AppShell>
  )
}

export function App() {
  return (
    <AuthProvider>
      <WorkspaceRouter />
    </AuthProvider>
  )
}
