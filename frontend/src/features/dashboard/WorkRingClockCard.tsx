import { useMemo, useState } from 'react'
import { CalendarPlus, Clock, Coffee, Laptop, MapPin, Pause, Play, Square, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { TodayClockSegmentResponse, WorkMode } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ManualDayClockSheet } from '@/features/attendance/ManualDayClockSheet'
import { useClockState } from '@/features/clock/ClockStateContext'
import { formatDurationHhMm } from '@/lib/spain'
import { cn } from '@/lib/utils'

interface RingGaugeProps {
  percent: number
  color: string
  trackColor?: string
  valueText: string
  label: string
  subLabel: string
}

function RingGauge({
  percent,
  color,
  trackColor = '#E2E8F0',
  valueText,
  label,
  subLabel,
}: RingGaugeProps) {
  const size = 96
  const strokeWidth = 8
  const radius = (size - strokeWidth) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = Math.max(0, Math.min(100, percent))
  const offset = circumference - (clamped / 100) * circumference

  return (
    <div className="flex flex-col items-center gap-1.5 text-center">
      <div className="relative flex h-24 w-24 items-center justify-center">
        <svg
          width={size}
          height={size}
          viewBox={`0 0 ${size} ${size}`}
          className="-rotate-90"
          aria-hidden="true"
        >
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={trackColor}
            strokeWidth={strokeWidth}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            className="transition-[stroke-dashoffset] duration-300"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center px-1">
          <span className="text-xs font-bold tracking-tight text-slate-900 tabular-nums">
            {valueText}
          </span>
        </div>
      </div>
      <div className="text-xs font-medium text-slate-700">{label}</div>
      <div className="text-[11px] text-slate-500 tabular-nums">{subLabel}</div>
    </div>
  )
}

function formatTimeShort(
  isoUtc: string,
  locale: string,
  timeZone: string,
): string {
  const date = new Date(isoUtc)
  if (Number.isNaN(date.getTime())) return '--:--'
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone,
  }).format(date)
}

export function WorkRingClockCard() {
  const { t, i18n } = useTranslation()
  const {
    hasEmployeeProfile,
    clockState,
    clockBusy,
    error: clockError,
    clearError,
    workMode,
    setWorkMode,
    liveWorkedMinutes,
    liveBreakMinutes,
    autoBreakRemainingSeconds,
    autoBreakCompleted,
    clearAutoBreakCompleted,
    performClockAction,
  } = useClockState()

  const locale = i18n.language || 'zh-CN'
  const isEs = locale.startsWith('es')
  const timeZone = 'Europe/Madrid'
  const [manualOpen, setManualOpen] = useState(false)
  const [manualSuccessNotice, setManualSuccessNotice] = useState<string | null>(
    null,
  )

  const status = clockState?.status ?? 'not_clocked_in'
  const isWorking = status === 'working' || status === 'remote'
  const isOnBreak = status === 'on_break'
  const isClockedOut = !isWorking && !isOnBreak

  const targetMinutes = clockState?.target_daily_minutes || 480
  const breakMinutes = liveBreakMinutes
  const effectivePolicy = clockState?.effective_break_policy ?? null
  const breakTargetMinutes =
    effectivePolicy?.effective_duration_minutes ??
    effectivePolicy?.duration_minutes ??
    clockState?.default_break_minutes ??
    60

  const effectiveRuleSummary = useMemo(() => {
    if (!effectivePolicy) return null
    const paidText = effectivePolicy.is_paid
      ? isEs
        ? 'Retribuido'
        : '带薪'
      : isEs
        ? 'No retribuido'
        : '不计薪'
    if (
      effectivePolicy.break_mode === 'fixed_window' &&
      effectivePolicy.windows.length > 0
    ) {
      const winStr = effectivePolicy.windows
        .map((w) => `${w.start}–${w.end}`)
        .join(', ')
      return isEs
        ? `Pausa programada: ${winStr} · ${paidText}`
        : `午休时段：${winStr} · ${paidText}`
    }
    const mins =
      effectivePolicy.effective_duration_minutes ??
      effectivePolicy.duration_minutes ??
      30
    return isEs
      ? `Descanso previsto: ${mins} min · ${paidText}`
      : `预设休息：${mins} 分钟 · ${paidText}`
  }, [effectivePolicy, isEs])

  const countdownFormatted = useMemo(() => {
    if (autoBreakRemainingSeconds === null) return null
    const m = Math.floor(autoBreakRemainingSeconds / 60)
    const s = autoBreakRemainingSeconds % 60
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  }, [autoBreakRemainingSeconds])

  const workedPercent = useMemo(() => {
    if (targetMinutes <= 0) return 0
    return Math.min(100, Math.round((liveWorkedMinutes / targetMinutes) * 100))
  }, [liveWorkedMinutes, targetMinutes])

  const breakPercent = useMemo(() => {
    if (breakMinutes <= 0 || breakTargetMinutes <= 0) return 0
    return Math.min(100, Math.round((breakMinutes / breakTargetMinutes) * 100))
  }, [breakMinutes, breakTargetMinutes])

  if (!hasEmployeeProfile || !clockState) {
    return null
  }

  const segments: TodayClockSegmentResponse[] = clockState.today_segments ?? []

  const statusBadge = isWorking
    ? {
        label:
          status === 'remote'
            ? t('clock.workMode.remote')
            : t('clock.status.working'),
        cls:
          status === 'remote'
            ? 'border-sky-200 bg-sky-50 text-sky-700'
            : 'border-emerald-200 bg-emerald-50 text-emerald-700',
      }
    : isOnBreak
      ? {
          label: t('clock.status.onBreak'),
          cls: 'border-amber-200 bg-amber-50 text-amber-800',
        }
      : {
          label: t('clock.status.clockedOut'),
          cls: 'border-slate-200 bg-slate-100 text-slate-600',
        }

  return (
    <Card className="gap-4 py-5">
      <CardHeader className="flex flex-row items-center justify-between gap-2 px-5 pb-0">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-900">
          <Clock className="h-4 w-4 text-[#2563EB]" aria-hidden="true" />
          <span>{isEs ? 'Registro de jornada' : '今日工时打卡'}</span>
        </CardTitle>
        <Badge variant="outline" className={cn('text-[11px]', statusBadge.cls)}>
          {statusBadge.label}
        </Badge>
      </CardHeader>

      <CardContent className="space-y-4 px-5">
        {clockError && (
          <div
            role="alert"
            aria-live="polite"
            className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700"
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

        {autoBreakCompleted && (
          <div
            role="status"
            className="flex items-center justify-between gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs text-sky-800"
          >
            <span>
              {isEs
                ? 'Descanso finalizado automáticamente. Jornada reanudada.'
                : '预设休息时间已到，系统已自动结束休息并恢复工作计时。'}
            </span>
            <button
              type="button"
              onClick={clearAutoBreakCompleted}
              aria-label={t('common.close')}
              className="shrink-0 text-sky-600 hover:text-sky-900"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        )}

        {manualSuccessNotice && (
          <div
            role="status"
            className="flex items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
          >
            <span>{manualSuccessNotice}</span>
            <button
              type="button"
              onClick={() => setManualSuccessNotice(null)}
              aria-label={t('common.close')}
              className="shrink-0 text-emerald-600 hover:text-emerald-900"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        )}

        {effectiveRuleSummary && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200/80 bg-slate-50/70 px-3 py-1.5 text-[11px] text-slate-600">
            <span className="flex items-center gap-1.5">
              <Coffee className="h-3.5 w-3.5 text-amber-600" aria-hidden="true" />
              {effectiveRuleSummary}
            </span>
            {isOnBreak && countdownFormatted && (
              <Badge
                variant="outline"
                className="border-amber-200 bg-amber-50 text-[11px] text-amber-800 tabular-nums"
              >
                {isEs
                  ? `Fin auto ${countdownFormatted}`
                  : `自动结束倒计时 ${countdownFormatted}`}
              </Badge>
            )}
          </div>
        )}

        {/* State-Locked Clock Actions */}
        <div className="flex flex-wrap items-center gap-2">
          {isClockedOut && (
            <>
              <Select
                value={workMode}
                onValueChange={(v) => setWorkMode(v as WorkMode)}
                disabled={clockBusy}
              >
                <SelectTrigger
                  className="h-9 min-w-28 flex-1 border-slate-200 bg-white text-xs"
                  aria-label={t('clock.workModeLabel')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="onsite">
                    <span className="inline-flex items-center gap-1.5">
                      <MapPin className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('clock.workMode.onsite')}
                    </span>
                  </SelectItem>
                  <SelectItem value="remote">
                    <span className="inline-flex items-center gap-1.5">
                      <Laptop className="h-3.5 w-3.5" aria-hidden="true" />
                      {t('clock.workMode.remote')}
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
              <Button
                type="button"
                size="sm"
                disabled={clockBusy}
                onClick={() => void performClockAction('clock_in')}
                className="h-9 flex-1 bg-[#2563EB] text-xs text-white hover:bg-blue-700"
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
                className="h-9 flex-1 border-amber-200 bg-amber-50 text-xs text-amber-800 hover:bg-amber-100"
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
                className="h-9 flex-1 border-rose-200 bg-rose-50 text-xs text-rose-700 hover:bg-rose-100"
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
                className="h-9 flex-1 border-amber-200 bg-amber-50 text-xs text-amber-800 hover:bg-amber-100"
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
                className="h-9 flex-1 border-rose-200 bg-rose-50 text-xs text-rose-700 hover:bg-rose-100"
              >
                <Square className="h-3.5 w-3.5" aria-hidden="true" />
                {t('clock.actions.clockOut')}
              </Button>
            </>
          )}
        </div>

        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => setManualOpen(true)}
          className="h-8 w-full border-slate-200 bg-slate-50/70 text-xs font-medium text-slate-700 hover:bg-slate-100"
        >
          <CalendarPlus className="h-3.5 w-3.5 text-[#2563EB]" aria-hidden="true" />
          {t('attendance.manualDay.trigger')}
        </Button>

        {/* Two Pure-SVG Progress Rings */}
        <div className="grid grid-cols-2 gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3">
          <RingGauge
            percent={workedPercent}
            color="#2563EB"
            valueText={formatDurationHhMm(liveWorkedMinutes)}
            label={isEs ? 'Trabajado' : '已工作'}
            subLabel={`${isEs ? 'Meta' : '目标'} ${formatDurationHhMm(targetMinutes)}`}
          />
          <RingGauge
            percent={breakPercent}
            color="#D97706"
            valueText={formatDurationHhMm(breakMinutes)}
            label={isEs ? 'Descanso' : '已休息'}
            subLabel={`${workedPercent}%`}
          />
        </div>

        {/* Real-time Today's Segments */}
        <div className="space-y-2">
          <div className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">
            {isEs ? 'Tramos de hoy' : '今日时段明细'}
          </div>
          {segments.length === 0 ? (
            <p className="rounded-lg border border-dashed border-slate-200 px-3 py-2.5 text-xs text-slate-500">
              {isEs
                ? 'Aún no hay fichajes registrados hoy.'
                : '今日暂无打卡分段记录。'}
            </p>
          ) : (
            <ul className="space-y-1.5">
              {segments.map((seg, idx) => {
                const startStr = formatTimeShort(
                  seg.start_utc,
                  locale,
                  timeZone,
                )
                const endStr = seg.end_utc
                  ? formatTimeShort(seg.end_utc, locale, timeZone)
                  : isEs
                    ? 'En curso'
                    : '进行中'
                const isBreak = seg.kind === 'break'
                return (
                  <li
                    key={`${seg.start_utc}-${idx}`}
                    className="flex items-center justify-between gap-2 rounded-lg border border-slate-200/75 bg-white px-2.5 py-1.5 text-xs"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      {isBreak ? (
                        <Coffee
                          className="h-3.5 w-3.5 shrink-0 text-amber-600"
                          aria-hidden="true"
                        />
                      ) : (
                        <Clock
                          className="h-3.5 w-3.5 shrink-0 text-[#2563EB]"
                          aria-hidden="true"
                        />
                      )}
                      <span className="font-medium text-slate-800">
                        {isBreak
                          ? isEs
                            ? 'Descanso'
                            : '休息'
                          : isEs
                            ? 'Trabajo'
                            : '工作'}
                      </span>
                      <span className="text-slate-500 tabular-nums">
                        {startStr} → {endStr}
                      </span>
                    </div>
                    <span className="shrink-0 font-medium text-slate-700 tabular-nums">
                      {formatDurationHhMm(seg.duration_minutes)}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </CardContent>
      <ManualDayClockSheet
        open={manualOpen}
        onOpenChange={setManualOpen}
        onSuccess={() => {
          setManualSuccessNotice(
            isEs
              ? 'Jornada registrada correctamente.'
              : '整日工时补录已保存并更新台账。',
          )
        }}
      />
    </Card>
  )
}
