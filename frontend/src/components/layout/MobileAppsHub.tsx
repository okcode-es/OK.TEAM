import {
  BarChart3,
  Building2,
  CalendarClock,
  CalendarDays,
  Clock,
  FileSpreadsheet,
  FileText,
  IdCard,
  Megaphone,
  Store as StoreIcon,
  Tablet,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { NavSection } from '@/components/layout/AppShell'
import { useAuth } from '@/features/auth/AuthContext'

interface MobileAppsHubProps {
  onNavigate: (section: NavSection) => void
}

export function MobileAppsHub({ onNavigate }: MobileAppsHubProps) {
  const { t } = useTranslation()
  const { activeTenant } = useAuth()
  const role = activeTenant?.role
  const canOperateKiosk =
    role === 'owner' || role === 'hr' || role === 'store_manager'
  const canViewPayroll = role !== 'employee'
  const canViewOrgAdmin = role !== 'employee'

  const items: Array<{ id: NavSection; label: string; icon: typeof Clock }> = [
    { id: 'attendance', label: t('nav.attendance'), icon: Clock },
    { id: 'leaves', label: t('nav.leaves'), icon: CalendarClock },
    { id: 'schedule', label: t('nav.schedule'), icon: CalendarDays },
    { id: 'documents', label: t('nav.documents'), icon: FileText },
    { id: 'announcements', label: t('nav.announcements'), icon: Megaphone },
    { id: 'employees', label: t('nav.employees'), icon: IdCard },
    ...(canViewPayroll
      ? [
          { id: 'payroll' as const, label: t('nav.payroll'), icon: FileSpreadsheet },
          { id: 'reports' as const, label: t('nav.reports'), icon: BarChart3 },
        ]
      : []),
    ...(canOperateKiosk
      ? [{ id: 'kiosk' as const, label: t('nav.kiosk'), icon: Tablet }]
      : []),
    ...(canViewOrgAdmin
      ? [
          { id: 'stores' as const, label: t('nav.stores'), icon: StoreIcon },
          { id: 'members' as const, label: t('nav.members'), icon: Users },
          {
            id: 'organization' as const,
            label: t('nav.organization'),
            icon: Building2,
          },
        ]
      : []),
  ]

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">
          {t('mobile.apps.title')}
        </h1>
        <p className="text-sm text-slate-600">{t('mobile.apps.subtitle')}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {items.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              className="flex flex-col items-start gap-2 rounded-xl border border-slate-200/75 bg-white p-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-colors hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
            >
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-[#2563EB]">
                <Icon className="h-4 w-4" aria-hidden="true" />
              </span>
              <span className="text-sm font-medium text-slate-900">
                {item.label}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
