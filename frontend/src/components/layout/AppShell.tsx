import { useEffect, useState, type ReactNode } from 'react'
import {
  BarChart3,
  Building2,
  CalendarClock,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  FileSpreadsheet,
  FileText,
  HelpCircle,
  Home,
  IdCard,
  LogOut,
  Megaphone,
  Menu,
  Pause,
  Play,
  Store as StoreIcon,
  Tablet,
  Users,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { UserAvatar } from '@/components/common/UserAvatar'
import { MobileBottomNav } from '@/components/layout/MobileBottomNav'
import { MobileClockFloatingBar } from '@/components/layout/MobileClockFloatingBar'
import { QuickClockBar } from '@/components/layout/QuickClockBar'
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
import { useClockState } from '@/features/clock/ClockStateContext'
import { cn } from '@/lib/utils'

export type NavSection =
  | 'dashboard'
  | 'employees'
  | 'attendance'
  | 'kiosk'
  | 'schedule'
  | 'leaves'
  | 'documents'
  | 'announcements'
  | 'payroll'
  | 'reports'
  | 'stores'
  | 'members'
  | 'organization'
  | 'apps'
  | 'profile'

const SIDEBAR_COLLAPSED_KEY = 'okteam.sidebar_collapsed'

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
  const {
    hasEmployeeProfile,
    employee,
    clockState,
    clockBusy,
    performClockAction,
  } = useClockState()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed))
    } catch {
      // ignore storage failure
    }
  }, [collapsed])

  const isEs = (i18n.language || 'zh-CN').startsWith('es')
  const canOperateKiosk =
    activeTenant?.role === 'owner' ||
    activeTenant?.role === 'hr' ||
    activeTenant?.role === 'store_manager'

  const canViewPayroll = activeTenant?.role !== 'employee'
  const canViewOrgAdmin = activeTenant?.role !== 'employee'
  const canViewOrgSettings = activeTenant?.role === 'owner'

  type NavItem = {
    id: NavSection
    label: string
    icon: typeof StoreIcon
  }

  type NavGroup = {
    id: string
    label: string
    items: NavItem[]
  }

  const navGroups: NavGroup[] = [
    {
      id: 'daily',
      label: isEs ? 'Gestión diaria' : '日常管理',
      items: [
        { id: 'dashboard', label: t('nav.dashboard'), icon: Home },
        { id: 'employees', label: t('nav.employees'), icon: IdCard },
        { id: 'attendance', label: t('nav.attendance'), icon: Clock },
        { id: 'schedule', label: t('nav.schedule'), icon: CalendarDays },
        { id: 'leaves', label: t('nav.leaves'), icon: CalendarClock },
        ...(canOperateKiosk
          ? [{ id: 'kiosk' as const, label: t('nav.kiosk'), icon: Tablet }]
          : []),
      ],
    },
    {
      id: 'compliance',
      label: isEs ? 'Cumplimiento' : '合规与行政',
      items: [
        { id: 'documents', label: t('nav.documents'), icon: FileText },
        { id: 'announcements', label: t('nav.announcements'), icon: Megaphone },
        ...(canViewPayroll
          ? [
              {
                id: 'payroll' as const,
                label: t('nav.payroll'),
                icon: FileSpreadsheet,
              },
              {
                id: 'reports' as const,
                label: t('nav.reports'),
                icon: BarChart3,
              },
            ]
          : []),
      ],
    },
    ...(canViewOrgAdmin
      ? [
          {
            id: 'config',
            label: isEs ? 'Configuración' : '企业配置',
            items: [
              { id: 'stores' as const, label: t('nav.stores'), icon: StoreIcon },
              { id: 'members' as const, label: t('nav.members'), icon: Users },
              ...(canViewOrgSettings
                ? [
                    {
                      id: 'organization' as const,
                      label: t('nav.organization'),
                      icon: Building2,
                    },
                  ]
                : []),
            ],
          },
        ]
      : []),
  ]

  const clockStatus = clockState?.status ?? 'not_clocked_in'
  const isWorking = clockStatus === 'working' || clockStatus === 'remote'
  const isOnBreak = clockStatus === 'on_break'

  const handleSidebarQuickClock = () => {
    if (!hasEmployeeProfile || !clockState || clockBusy) return
    if (isWorking) {
      void performClockAction('break_start')
    } else if (isOnBreak) {
      void performClockAction('break_end')
    } else {
      void performClockAction('clock_in')
    }
  }

  const displayName =
    employee?.preferred_name ||
    employee?.full_name ||
    activeTenant?.display_name ||
    ''

  const renderDesktopNavList = () => (
    <nav className="flex flex-col gap-0.5" aria-label="Main navigation">
      {navGroups.map((group, groupIdx) => (
        <div key={group.id} className={cn(groupIdx > 0 && !collapsed && 'mt-1')}>
          {!collapsed && (
            <div className="px-2.5 pt-3 pb-1 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">
              {group.label}
            </div>
          )}
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = activeSection === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  title={collapsed ? item.label : undefined}
                  aria-current={isActive ? 'page' : undefined}
                  onClick={() => onNavigate(item.id)}
                  className={cn(
                    'flex items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-left text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
                    collapsed && 'justify-center px-2',
                    isActive
                      ? 'bg-[#2563EB] text-white'
                      : 'text-slate-300 hover:bg-slate-900 hover:text-white',
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  {!collapsed && <span className="truncate">{item.label}</span>}
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )

  const renderMobileNavList = () => (
    <nav className="flex flex-col gap-0.5" aria-label="Mobile navigation">
      {navGroups.map((group) => (
        <div key={group.id}>
          <div className="px-3 pt-3 pb-1 text-[11px] font-semibold tracking-wider text-slate-500 uppercase">
            {group.label}
          </div>
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = activeSection === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  aria-current={isActive ? 'page' : undefined}
                  onClick={() => {
                    onNavigate(item.id)
                    setMobileMenuOpen(false)
                  }}
                  className={cn(
                    'flex items-center gap-2.5 rounded-lg px-3 py-[7px] text-left text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
                    isActive
                      ? 'bg-[#2563EB] text-white'
                      : 'text-slate-300 hover:bg-slate-900 hover:text-white',
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="truncate">{item.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      ))}
    </nav>
  )

  return (
    <div className="flex min-h-screen bg-[#F3F5F9] text-slate-900">
      {/* Desktop Collapsible Left Sidebar (Deep Navy #0B1120) */}
      <aside
        className={cn(
          'sticky top-0 hidden h-screen shrink-0 flex-col bg-[#0B1120] text-slate-300 transition-[width] duration-200 lg:flex',
          collapsed ? 'w-16' : 'w-60',
        )}
      >
        <div className="shrink-0 border-b border-slate-800/80">
          <div
            className={cn(
              'flex h-14 items-center px-3',
              collapsed ? 'justify-center' : 'justify-between',
            )}
          >
            {!collapsed && (
              <div className="flex min-w-0 items-center gap-2.5 px-1">
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[#2563EB] text-xs font-bold text-white"
                  aria-hidden="true"
                >
                  ok
                </span>
                <span
                  translate="no"
                  className="truncate text-sm font-semibold tracking-tight text-white"
                >
                  {t('brand')}
                </span>
              </div>
            )}
            <button
              type="button"
              onClick={() => setCollapsed((prev) => !prev)}
              aria-label={
                isEs
                  ? collapsed
                    ? 'Expandir barra lateral'
                    : 'Contraer barra lateral'
                  : collapsed
                    ? '展开侧边栏'
                    : '收起侧边栏'
              }
              title={
                isEs
                  ? collapsed
                    ? 'Expandir barra lateral'
                    : 'Contraer barra lateral'
                  : collapsed
                    ? '展开侧边栏'
                    : '收起侧边栏'
              }
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-900 hover:text-white focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
            >
              {collapsed ? (
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              ) : (
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          </div>

          {!collapsed &&
            profile &&
            profile.tenants.length > 0 &&
            activeTenant && (
              <div className="space-y-1 px-3 pb-2.5">
                <span className="px-1 text-[10px] font-medium tracking-wider text-slate-400 uppercase">
                  {t('nav.switchTenant')}
                </span>
                <Select
                  value={activeTenant.tenant_id}
                  onValueChange={selectTenant}
                >
                  <SelectTrigger className="h-8 w-full border-slate-800 bg-slate-900/90 text-left text-xs text-slate-200">
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
              </div>
            )}
        </div>

        <div className="sidebar-scroll min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
          {renderDesktopNavList()}
        </div>

        {/* Bottom Pinned: Help + Avatar (360° Profile) + Quick Clock Play/Pause + Sign Out */}
        <div className="relative shrink-0 border-t border-slate-800/80 p-2.5">
          {helpOpen && (
            <div
              role="dialog"
              aria-label={isEs ? 'Ayuda y cumplimiento' : '帮助与合规提示'}
              className="absolute bottom-14 left-2.5 z-50 w-64 rounded-xl border border-slate-200/75 bg-white p-3.5 text-slate-800 shadow-md"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-slate-900">
                  {isEs ? 'Cumplimiento & Atajos' : '合规提示与快捷操作'}
                </span>
                <button
                  type="button"
                  onClick={() => setHelpOpen(false)}
                  aria-label={t('common.close')}
                  className="rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" />
                </button>
              </div>
              <ul className="space-y-1.5 text-[11px] leading-relaxed text-slate-600">
                <li>
                  •{' '}
                  {isEs
                    ? 'Registro diario obligatorio (RDL 8/2019), retención legal de 4 años.'
                    : '依法执行每日工时登记（RDL 8/2019），记录强制留存 4 年。'}
                </li>
                <li>
                  •{' '}
                  {isEs
                    ? 'Pulse ▶ / ⏸ abajo para iniciar o pausar su jornada.'
                    : '点击左下角 ▶ / ⏸ 可快速开始工作或暂停休息。'}
                </li>
                <li>
                  •{' '}
                  {isEs
                    ? 'Haga clic en su avatar para abrir su perfil 360°.'
                    : '点击左下角头像可直达个人 360° 档案页。'}
                </li>
              </ul>
            </div>
          )}

          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <button
                type="button"
                onClick={() => setHelpOpen((v) => !v)}
                aria-label={isEs ? 'Ayuda' : '帮助'}
                title={isEs ? 'Ayuda' : '帮助'}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-900 hover:text-white"
              >
                <HelpCircle className="h-4 w-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => onNavigate('profile')}
                title={displayName}
                aria-label={displayName}
                className="rounded-full ring-1 ring-slate-700 transition-opacity hover:opacity-90"
              >
                <UserAvatar
                  avatarUrl={employee?.avatar_url}
                  name={displayName}
                  size="sm"
                />
              </button>
              {hasEmployeeProfile && clockState && (
                <button
                  type="button"
                  disabled={clockBusy}
                  onClick={handleSidebarQuickClock}
                  aria-label={
                    isWorking
                      ? t('clock.actions.pause')
                      : isOnBreak
                        ? t('clock.actions.resume')
                        : t('clock.actions.clockIn')
                  }
                  title={
                    isWorking
                      ? t('clock.actions.pause')
                      : isOnBreak
                        ? t('clock.actions.resume')
                        : t('clock.actions.clockIn')
                  }
                  className={cn(
                    'flex h-8 w-8 items-center justify-center rounded-lg text-white transition-colors disabled:opacity-50',
                    isWorking
                      ? 'bg-amber-500 text-slate-950 hover:bg-amber-400'
                      : 'bg-[#2563EB] hover:bg-blue-500',
                  )}
                >
                  {isWorking ? (
                    <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                  ) : (
                    <Play className="h-3.5 w-3.5" aria-hidden="true" />
                  )}
                </button>
              )}
              <button
                type="button"
                onClick={() => void signOut()}
                aria-label={t('nav.signOut')}
                title={t('nav.signOut')}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-900 hover:text-white"
              >
                <LogOut className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 px-1">
                <button
                  type="button"
                  onClick={() => onNavigate('profile')}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-lg p-1 text-left transition-colors hover:bg-slate-900 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
                  title={displayName}
                >
                  <UserAvatar
                    avatarUrl={employee?.avatar_url}
                    name={displayName}
                    size="sm"
                    className="border-slate-700"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-white">
                      {displayName}
                    </div>
                    {activeTenant && (
                      <div className="truncate text-[11px] text-slate-400">
                        {t(`roles.${activeTenant.role}`)}
                      </div>
                    )}
                  </div>
                </button>

                {hasEmployeeProfile && clockState && (
                  <button
                    type="button"
                    disabled={clockBusy}
                    onClick={handleSidebarQuickClock}
                    aria-label={
                      isWorking
                        ? t('clock.actions.pause')
                        : isOnBreak
                          ? t('clock.actions.resume')
                          : t('clock.actions.clockIn')
                    }
                    title={
                      isWorking
                        ? t('clock.actions.pause')
                        : isOnBreak
                          ? t('clock.actions.resume')
                          : t('clock.actions.clockIn')
                    }
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-white transition-colors disabled:opacity-50',
                      isWorking
                        ? 'bg-amber-500 text-slate-950 hover:bg-amber-400'
                        : 'bg-[#2563EB] hover:bg-blue-500',
                    )}
                  >
                    {isWorking ? (
                      <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                    ) : (
                      <Play className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                  </button>
                )}
              </div>

              <div className="flex items-center justify-between gap-1">
                <button
                  type="button"
                  onClick={() => setHelpOpen((v) => !v)}
                  className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-900 hover:text-white"
                >
                  <HelpCircle className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{isEs ? 'Ayuda' : '帮助'}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-slate-400 transition-colors hover:bg-slate-900 hover:text-white"
                >
                  <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>{t('nav.signOut')}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-slate-800 bg-[#0B1120] px-4 text-white lg:border-slate-200 lg:bg-white lg:px-6 lg:text-slate-900">
          <div className="flex min-w-0 items-center gap-2">
            <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
              <SheetTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="text-slate-200 hover:bg-slate-900 hover:text-white lg:hidden"
                  aria-label={t('nav.openMenu')}
                >
                  <Menu className="h-4 w-4" aria-hidden="true" />
                </Button>
              </SheetTrigger>
              <SheetContent
                side="left"
                className="w-72 border-slate-800 bg-[#0B1120] p-0 text-slate-200"
              >
                <SheetHeader className="border-b border-slate-800 px-4 py-3.5">
                  <SheetTitle className="flex items-center gap-2 text-sm font-semibold text-white">
                    <span
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[#2563EB] text-xs font-bold text-white"
                      aria-hidden="true"
                    >
                      ok
                    </span>
                    {t('brand')}
                  </SheetTitle>
                </SheetHeader>
                <div className="sidebar-scroll flex flex-1 flex-col gap-4 overflow-y-auto p-4">
                  {profile && activeTenant && (
                    <Select
                      value={activeTenant.tenant_id}
                      onValueChange={selectTenant}
                    >
                      <SelectTrigger className="w-full border-slate-800 bg-slate-900 text-slate-100">
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
                  {renderMobileNavList()}
                  <Separator className="bg-slate-800" />
                  <div className="flex items-center justify-between gap-2 px-1">
                    <span className="truncate text-xs text-slate-400">
                      {displayName}
                    </span>
                    {activeTenant && (
                      <Badge
                        variant="outline"
                        className="border-slate-700 bg-slate-900 text-slate-200"
                      >
                        {t(`roles.${activeTenant.role}`)}
                      </Badge>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="border-slate-700 bg-slate-900 text-slate-200 hover:bg-slate-800 hover:text-white"
                    onClick={() => void signOut()}
                  >
                    <LogOut className="h-4 w-4" aria-hidden="true" />
                    {t('nav.signOut')}
                  </Button>
                </div>
              </SheetContent>
            </Sheet>

            <div className="min-w-0">
              <p className="truncate text-sm font-semibold text-white lg:text-slate-900">
                {activeTenant?.tenant_trade_name ?? t('brand')}
              </p>
              <p className="hidden truncate text-xs text-slate-400 sm:block lg:text-slate-500">
                {activeTenant?.tenant_legal_name ?? ''}
              </p>
            </div>
          </div>

          <QuickClockBar onOpenProfile={() => onNavigate('profile')} />
        </header>

        <main className="mx-auto w-full max-w-7xl flex-1 p-4 pb-36 sm:p-6 lg:pb-8">
          {children}
        </main>
      </div>

      <MobileClockFloatingBar />
      <MobileBottomNav activeSection={activeSection} onNavigate={onNavigate} />
    </div>
  )
}
