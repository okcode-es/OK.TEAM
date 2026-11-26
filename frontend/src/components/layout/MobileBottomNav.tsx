import { Grid, Home, User } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { NavSection } from '@/components/layout/AppShell'
import { cn } from '@/lib/utils'

interface MobileBottomNavProps {
  activeSection: NavSection
  onNavigate: (section: NavSection) => void
}

export function MobileBottomNav({
  activeSection,
  onNavigate,
}: MobileBottomNavProps) {
  const { t } = useTranslation()

  const tabs: Array<{
    id: NavSection
    label: string
    icon: typeof Home
    isActive: boolean
  }> = [
    {
      id: 'dashboard',
      label: t('mobile.nav.home'),
      icon: Home,
      isActive: activeSection === 'dashboard',
    },
    {
      id: 'apps',
      label: t('mobile.nav.apps'),
      icon: Grid,
      isActive:
        activeSection === 'apps' ||
        (activeSection !== 'dashboard' && activeSection !== 'profile'),
    },
    {
      id: 'profile',
      label: t('mobile.nav.profile'),
      icon: User,
      isActive: activeSection === 'profile',
    },
  ]

  return (
    <nav
      aria-label={t('mobile.nav.ariaLabel')}
      className="fixed right-0 bottom-0 left-0 z-30 grid h-16 grid-cols-3 border-t border-slate-200 bg-white lg:hidden"
    >
      {tabs.map((tab) => {
        const Icon = tab.icon
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => onNavigate(tab.id)}
            aria-current={tab.isActive ? 'page' : undefined}
            className={cn(
              'flex flex-col items-center justify-center gap-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
              tab.isActive ? 'text-[#2563EB]' : 'text-slate-500',
            )}
          >
            <Icon className="h-5 w-5" aria-hidden="true" />
            <span className="truncate px-1">{tab.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
