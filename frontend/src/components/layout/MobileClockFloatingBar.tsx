import { Pause, Play, Square, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { WorkMode } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useClockState } from '@/features/clock/ClockStateContext'
import { formatDurationHhMm } from '@/lib/spain'

export function MobileClockFloatingBar() {
  const { t } = useTranslation()
  const {
    hasEmployeeProfile,
    clockState,
    clockBusy,
    error: clockError,
    clearError,
    workMode,
    setWorkMode,
    liveWorkedMinutes,
    performClockAction,
  } = useClockState()

  if (!hasEmployeeProfile || !clockState) {
    return null
  }

  const status = clockState.status ?? 'not_clocked_in'
  const isWorking = status === 'working' || status === 'remote'
  const isOnBreak = status === 'on_break'
  const isClockedOut = !isWorking && !isOnBreak
  const statusLabel = isWorking
    ? t('clock.status.working')
    : isOnBreak
      ? t('clock.status.onBreak')
      : t('clock.status.clockedOut')

  return (
    <div className="fixed right-3 bottom-[4.25rem] left-3 z-30 flex flex-col gap-1.5 lg:hidden">
      {clockError && (
        <div
          role="alert"
          aria-live="polite"
          className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 shadow-sm"
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
      <div className="flex items-center justify-between gap-2 rounded-xl border border-slate-800 bg-[#0B1120] px-3.5 py-2.5 text-white shadow-sm">
        <div className="min-w-0">
          <div className="text-sm font-semibold tracking-tight tabular-nums">
            {formatDurationHhMm(liveWorkedMinutes)}
          </div>
          <div className="truncate text-[11px] text-slate-300">
            {statusLabel}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {isClockedOut && (
            <>
              <button
                type="button"
                disabled={clockBusy}
                onClick={() =>
                  setWorkMode(
                    (workMode === 'onsite' ? 'remote' : 'onsite') as WorkMode,
                  )
                }
                className="rounded-md border border-slate-700 bg-slate-800 px-2.5 py-1.5 text-xs font-medium text-slate-200"
              >
                {t(`clock.workMode.${workMode}`)}
              </button>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_in')}
                className="h-8 bg-[#2563EB] px-3 text-xs text-white hover:bg-blue-700"
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
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_out')}
                className="h-8 bg-rose-600 px-3 text-xs text-white hover:bg-rose-500"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockOut')}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('break_start')}
                className="h-8 bg-amber-500 px-3 text-xs text-slate-950 hover:bg-amber-400"
              >
                <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.pause')}
              </Button>
            </>
          )}

          {isOnBreak && (
            <>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_out')}
                className="h-8 bg-rose-600 px-3 text-xs text-white hover:bg-rose-500"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockOut')}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('break_end')}
                className="h-8 bg-amber-500 px-3 text-xs text-slate-950 hover:bg-amber-400"
              >
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.resume')}
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
