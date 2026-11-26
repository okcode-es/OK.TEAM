import { Globe, Pause, Play, Square, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { WorkMode } from '@/api/types'
import { UserAvatar } from '@/components/common/UserAvatar'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAuth } from '@/features/auth/AuthContext'
import { useClockState } from '@/features/clock/ClockStateContext'
import { SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'
import { formatDurationHhMm } from '@/lib/spain'

interface QuickClockBarProps {
  onOpenProfile?: () => void
}

export function QuickClockBar({ onOpenProfile }: QuickClockBarProps) {
  const { t, i18n } = useTranslation()
  const { activeTenant } = useAuth()
  const {
    hasEmployeeProfile,
    employee,
    clockState,
    clockBusy,
    error: clockError,
    clearError,
    workMode,
    setWorkMode,
    liveWorkedMinutes,
    performClockAction,
  } = useClockState()

  const activeLocale = (i18n.language as SupportedLocale) || 'zh-CN'
  const status = clockState?.status ?? 'not_clocked_in'
  const isWorking = status === 'working' || status === 'remote'
  const isOnBreak = status === 'on_break'
  const isClockedOut = !isWorking && !isOnBreak
  const targetMinutes = clockState?.target_daily_minutes ?? 480
  const displayName =
    employee?.preferred_name ||
    employee?.full_name ||
    activeTenant?.display_name ||
    ''

  return (
    <div className="relative flex shrink-0 items-center gap-2">
      {clockError && (
        <div
          role="alert"
          aria-live="polite"
          className="fixed top-14 right-4 z-50 flex max-w-sm items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 shadow-sm"
        >
          <span className="flex-1">{clockError}</span>
          <button
            type="button"
            onClick={clearError}
            aria-label={t('common.close')}
            className="shrink-0 text-rose-500 hover:text-rose-800"
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      )}
      {/* Desktop Quick Clock Controls */}
      {hasEmployeeProfile && clockState && (
        <div className="hidden items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 lg:flex">
          <span className="text-xs font-medium text-slate-700 tabular-nums">
            {formatDurationHhMm(liveWorkedMinutes)} /{' '}
            {formatDurationHhMm(targetMinutes)}
          </span>

          {isClockedOut && (
            <>
              <Select
                value={workMode}
                onValueChange={(v) => setWorkMode(v as WorkMode)}
                disabled={clockBusy}
              >
                <SelectTrigger
                  className="h-7 min-w-24 border-slate-200 bg-white px-2 text-xs"
                  aria-label={t('clock.workModeLabel')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="onsite">
                    {t('clock.workMode.onsite')}
                  </SelectItem>
                  <SelectItem value="remote">
                    {t('clock.workMode.remote')}
                  </SelectItem>
                </SelectContent>
              </Select>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_in')}
                className="h-7 bg-[#2563EB] px-3 text-xs text-white hover:bg-blue-700"
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockIn')}
              </Button>
            </>
          )}

          {isWorking && (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={clockBusy}
                onClick={() => void performClockAction('break_start')}
                className="h-7 border-amber-200 bg-amber-50 px-2.5 text-xs text-amber-800 hover:bg-amber-100"
              >
                <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.pause')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_out')}
                className="h-7 border-rose-200 bg-rose-50 px-2.5 text-xs text-rose-700 hover:bg-rose-100"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockOut')}
              </Button>
            </>
          )}

          {isOnBreak && (
            <>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={clockBusy}
                onClick={() => void performClockAction('break_end')}
                className="h-7 border-amber-200 bg-amber-50 px-2.5 text-xs text-amber-800 hover:bg-amber-100"
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.resume')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_out')}
                className="h-7 border-rose-200 bg-rose-50 px-2.5 text-xs text-rose-700 hover:bg-rose-100"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockOut')}
              </Button>
            </>
          )}
        </div>
      )}

      {/* Compact Language Switcher */}
      <div
        className="flex shrink-0 items-center gap-0.5 rounded-md border border-slate-700 bg-slate-900 p-0.5 lg:border-slate-200 lg:bg-white"
        role="group"
        aria-label={t('locale.label')}
      >
        <Globe
          className="mx-1 hidden h-3.5 w-3.5 text-slate-400 sm:block"
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
            className={
              activeLocale === locale
                ? 'h-6 bg-[#2563EB] px-2 text-xs text-white hover:bg-blue-700'
                : 'h-6 px-2 text-xs text-slate-300 hover:bg-slate-800 hover:text-white lg:text-slate-600 lg:hover:bg-slate-100 lg:hover:text-slate-900'
            }
          >
            {locale === 'zh-CN' ? '中文' : 'ES'}
          </Button>
        ))}
      </div>

      {/* User Avatar */}
      <div className="hidden items-center gap-2 pl-1 lg:flex">
        <button
          type="button"
          onClick={onOpenProfile}
          className="flex items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none"
          title={displayName}
        >
          <UserAvatar
            avatarUrl={employee?.avatar_url}
            name={displayName}
            size="sm"
          />
          <span className="max-w-28 truncate text-xs font-medium text-slate-700">
            {displayName}
          </span>
        </button>
      </div>
    </div>
  )
}
