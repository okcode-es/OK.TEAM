import { useState, type ReactNode } from 'react'
import {
  Building2,
  CalendarClock,
  CalendarDays,
  Clock,
  FileSpreadsheet,
  FileText,
  Globe,
  IdCard,
  LogOut,
  Menu,
  Store as StoreIcon,
  Tablet,
  Users,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { useAuth } from '@/features/auth/AuthContext'
import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
import { cn } from '@/lib/utils'

export type NavSection =
  | 'employees'
  | 'attendance'
  | 'kiosk'
  | 'schedule'
  | 'leaves'
  | 'documents'
  | 'payroll'
  | 'stores'
  | 'members'
  | 'organization'

interface AppShellProps {
  activeSection: NavSection
  onNavigate: (section: NavSection) => void
  children: ReactNode
}

export function AppShell({
  activeSection,
  onNavigate,
  children,
}: AppShellProps) {
  const { t, i18n } = useTranslation()
  const { profile, activeTenant, selectTenant, signOut } = useAuth()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  const activeLocale = (i18n.language as SupportedLocale) || 'zh-CN'

  const canOperateKiosk =
    activeTenant?.role === 'owner' ||
    activeTenant?.role === 'hr' ||
    activeTenant?.role === 'store_manager'

  const canViewPayroll = activeTenant?.role !== 'employee'

  const navItems: Array<{
    id: NavSection
    label: string
    icon: typeof StoreIcon
  }> = [
    { id: 'employees', label: t('nav.employees'), icon: IdCard },
    { id: 'attendance', label: t('nav.attendance'), icon: Clock },
    ...(canOperateKiosk
      ? [{ id: 'kiosk' as const, label: t('nav.kiosk'), icon: Tablet }]
      : []),
    { id: 'schedule', label: t('nav.schedule'), icon: CalendarDays },
    { id: 'leaves', label: t('nav.leaves'), icon: CalendarClock },
    { id: 'documents', label: t('nav.documents'), icon: FileText },
    ...(canViewPayroll
      ? [{ id: 'payroll' as const, label: t('nav.payroll'), icon: FileSpreadsheet }]
      : []),
    { id: 'stores', label: t('nav.stores'), icon: StoreIcon },
    { id: 'members', label: t('nav.members'), icon: Users },
    { id: 'organization', label: t('nav.organization'), icon: Building2 },
  ]

  const renderNavList = (closeAfterClick?: boolean) => (
    <nav className="flex flex-col gap-1" aria-label="Main navigation">
      {navItems.map((item) => {
        const Icon = item.icon
        const isActive = activeSection === item.id
        return (
          <button
            key={item.id}
            type="button"
            aria-current={isActive ? 'page' : undefined}
            onClick={() => {
              onNavigate(item.id)
              if (closeAfterClick) {
                setMobileMenuOpen(false)
              }
            }}
            className={cn(
              'flex items-center gap-2.5 rounded-md px-3 py-2 text-left text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-teal-700 focus-visible:outline-none',
              isActive
                ? 'bg-slate-100 text-slate-900'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
            )}
          >
            <span
              className={cn(
                'h-4 w-1 shrink-0 rounded-full',
                isActive ? 'bg-teal-700' : 'bg-transparent',
              )}
              aria-hidden="true"
            />
            <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="truncate">{item.label}</span>
          </button>
        )
      })}
    </nav>
  )

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900">
      {/* Desktop Left Sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
        <div className="flex h-14 items-center gap-2.5 border-b border-slate-200 px-4">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full bg-teal-700"
            aria-hidden="true"
          />
          <span
            translate="no"
            className="text-sm font-semibold tracking-tight text-slate-900"
          >
            {t('brand')}
          </span>
        </div>

        <div className="flex flex-1 flex-col gap-4 p-3">
          {profile && profile.tenants.length > 0 && activeTenant && (
            <div className="space-y-1.5">
              <span className="px-1 text-[11px] font-medium tracking-wide text-slate-500 uppercase">
                {t('nav.switchTenant')}
              </span>
              <Select
                value={activeTenant.tenant_id}
                onValueChange={selectTenant}
              >
                <SelectTrigger className="w-full bg-white text-left">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {profile.tenants.map((tenant) => (
                    <SelectItem key={tenant.tenant_id} value={tenant.tenant_id}>
                      {tenant.tenant_trade_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {renderNavList(false)}
        </div>

        <div className="border-t border-slate-200 p-3">
          <div className="mb-2 flex items-center justify-between gap-2 px-1">
            <span className="truncate text-xs text-slate-600">
              {profile?.email ?? ''}
            </span>
            {activeTenant && (
              <Badge
                variant="outline"
                className="border-slate-200 bg-slate-100 text-slate-700"
              >
                {t(`roles.${activeTenant.role}`)}
              </Badge>
            )}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-full justify-start text-slate-600"
            onClick={() => void signOut()}
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
            {t('nav.signOut')}
          </Button>
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
              <SheetTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="lg:hidden"
                  aria-label={t('nav.openMenu')}
                >
                  <Menu className="h-4 w-4" aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent side="left" className="w-72 bg-white p-0">
                <SheetHeader className="border-b border-slate-200 px-4 py-3.5">
                  <SheetTitle className="flex items-center gap-2 text-sm font-semibold">
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full bg-teal-700"
                      aria-hidden="true"
                    />
                    {t('brand')}
                  </SheetTitle>
                </SheetHeader>
                <div className="flex flex-1 flex-col gap-4 p-4">
                  {profile && activeTenant && (
                    <Select
                      value={activeTenant.tenant_id}
                      onValueChange={selectTenant}
                    >
                      <SelectTrigger className="w-full bg-white">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {profile.tenants.map((tenant) => (
                          <SelectItem
                            key={tenant.tenant_id}
                            value={tenant.tenant_id}
                          >
                            {tenant.tenant_trade_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  {renderNavList(true)}
                  <Separator />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => void signOut()}
                  >
                    <LogOut className="h-4 w-4" aria-hidden="true" />
                    {t('nav.signOut')}
                  </Button>
                </div>
              </SheetContent>
            </Sheet>

            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-slate-900">
                {activeTenant?.tenant_trade_name ?? t('brand')}
              </p>
              <p className="hidden truncate text-xs text-slate-500 sm:block">
                {activeTenant?.tenant_legal_name ?? ''}
              </p>
            </div>
          </div>

          <div
            className="flex shrink-0 items-center gap-1"
            role="group"
            aria-label={t('locale.label')}
          >
            <Globe
              className="mr-1 hidden h-4 w-4 text-slate-400 sm:block"
              aria-hidden="true"
            />
            {SUPPORTED_LOCALES.map((locale) => (
              <Button
                key={locale}
                type="button"
                size="sm"
                variant={activeLocale === locale ? 'default' : 'ghost'}
                aria-pressed={activeLocale === locale}
                onClick={() => void i18n.changeLanguage(locale)}
              >
                {t(`locale.${locale}`)}
              </Button>
            ))}
          </div>
        </header>

        <main className="mx-auto w-full max-w-7xl flex-1 p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  )
}
