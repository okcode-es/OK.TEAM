import { useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AttendanceSegmentResponse } from '@/api/types'
import { cn } from '@/lib/utils'

interface SegmentedProgressBarProps {
  segments: AttendanceSegmentResponse[]
  plannedMinutes: number
  dailyLimitMinutes: number
  timeZone: string
  locale: string
}

type VisualSegmentKind = 'work' | 'overtime' | 'break'

interface VisualSegment {
  id: string
  kind: VisualSegmentKind
  startAt: string
  endAt: string | null
  durationMinutes: number
  isPaid: boolean
  widthPct: number
  centerPct: number
  tooltipText: string
  ariaLabel: string
}

function formatHm(
  isoUtc: string | null,
  locale: string,
  timeZone: string,
  openLabel: string,
): string {
  if (!isoUtc) return openLabel
  const dt = new Date(isoUtc)
  if (Number.isNaN(dt.getTime())) return openLabel
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone,
  }).format(dt)
}

function formatCompactDuration(minutes: number): string {
  const safe = Math.max(0, Math.round(minutes))
  const h = Math.floor(safe / 60)
  const m = safe % 60
  if (h > 0 && m > 0) return `${h}h ${m}m`
  if (h > 0) return `${h}h`
  return `${m}m`
}

export function SegmentedProgressBar({
  segments,
  plannedMinutes,
  dailyLimitMinutes,
  timeZone,
  locale,
}: SegmentedProgressBarProps) {
  const { t } = useTranslation()
  const isEs = locale.startsWith('es')
  const [activeIndex, setActiveIndex] = useState<number | null>(null)
  const closeTimerRef = useRef<number | null>(null)

  const openTooltip = (idx: number) => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
    setActiveIndex(idx)
  }

  const scheduleClose = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
    }
    closeTimerRef.current = window.setTimeout(() => {
      setActiveIndex(null)
      closeTimerRef.current = null
    }, 80)
  }

  const cancelClose = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current)
      closeTimerRef.current = null
    }
  }

  const visualSegments = useMemo<VisualSegment[]>(() => {
    if (!segments || segments.length === 0) return []

    const rawParts: Array<{
      kind: VisualSegmentKind
      startAt: string
      endAt: string | null
      durationMinutes: number
      isPaid: boolean
    }> = []

    let cumWork = 0
    const limit = dailyLimitMinutes > 0 ? dailyLimitMinutes : 540

    for (const seg of segments) {
      const dur = Math.max(0, seg.duration_minutes)
      if (dur === 0 && seg.end_at !== null) continue

      if (seg.type === 'break') {
        rawParts.push({
          kind: 'break',
          startAt: seg.start_at,
          endAt: seg.end_at,
          durationMinutes: dur,
          isPaid: seg.is_paid,
        })
        continue
      }

      // Work segment: check if it crosses dailyLimitMinutes
      if (cumWork >= limit) {
        rawParts.push({
          kind: 'overtime',
          startAt: seg.start_at,
          endAt: seg.end_at,
          durationMinutes: dur,
          isPaid: true,
        })
        cumWork += dur
      } else if (cumWork + dur > limit) {
        const regularMin = limit - cumWork
        const overtimeMin = dur - regularMin
        const startMs = new Date(seg.start_at).getTime()
        const splitIso = Number.isNaN(startMs)
          ? seg.end_at
          : new Date(startMs + regularMin * 60_000).toISOString()

        rawParts.push({
          kind: 'work',
          startAt: seg.start_at,
          endAt: splitIso,
          durationMinutes: regularMin,
          isPaid: true,
        })
        rawParts.push({
          kind: 'overtime',
          startAt: splitIso ?? seg.start_at,
          endAt: seg.end_at,
          durationMinutes: overtimeMin,
          isPaid: true,
        })
        cumWork += dur
      } else {
        rawParts.push({
          kind: 'work',
          startAt: seg.start_at,
          endAt: seg.end_at,
          durationMinutes: dur,
          isPaid: true,
        })
        cumWork += dur
      }
    }

    const totalVisualMinutes = rawParts.reduce(
      (acc, p) => acc + Math.max(p.durationMinutes, 1),
      0,
    )
    const denominator = Math.max(plannedMinutes, totalVisualMinutes, 480)
    const limitHours = Math.round((limit / 60) * 10) / 10
    const openLabel = t('attendance.inProgress')

    let offsetPct = 0
    return rawParts.map((part, idx) => {
      const effectiveDur = Math.max(part.durationMinutes, 2)
      const widthPct = Math.min(100 - offsetPct, (effectiveDur / denominator) * 100)
      const centerPct = Math.min(92, Math.max(8, offsetPct + widthPct / 2))
      offsetPct += widthPct

      const startHm = formatHm(part.startAt, locale, timeZone, openLabel)
      const endHm = formatHm(part.endAt, locale, timeZone, openLabel)
      const durText = formatCompactDuration(part.durationMinutes)

      let labelPrefix = isEs ? 'Trabajo' : '工作'
      if (part.kind === 'overtime') {
        labelPrefix = isEs
          ? `Exceso (>${limitHours}h)`
          : `超时 (>${limitHours}h)`
      } else if (part.kind === 'break') {
        labelPrefix = isEs ? 'Descanso' : '休息'
      }

      const paidSuffix =
        part.kind === 'break'
          ? part.isPaid
            ? isEs
              ? ' (remunerado)'
              : ' (计薪)'
            : isEs
              ? ' (no remunerado)'
              : ' (不计薪)'
          : ''

      const tooltipText = `${labelPrefix} ${startHm}–${endHm} · ${durText}${paidSuffix}`

      return {
        id: `${idx}-${part.kind}-${part.startAt}`,
        kind: part.kind,
        startAt: part.startAt,
        endAt: part.endAt,
        durationMinutes: part.durationMinutes,
        isPaid: part.isPaid,
        widthPct,
        centerPct,
        tooltipText,
        ariaLabel: tooltipText,
      }
    })
  }, [segments, plannedMinutes, dailyLimitMinutes, timeZone, locale, isEs, t])

  const activeSegment =
    activeIndex !== null ? visualSegments[activeIndex] ?? null : null

  return (
    <div
      className="relative w-full min-w-36 py-1"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && activeIndex !== null) {
          e.stopPropagation()
          setActiveIndex(null)
        }
      }}
    >
      {activeSegment && (
        <div
          role="tooltip"
          onMouseEnter={cancelClose}
          onMouseLeave={scheduleClose}
          style={{ left: `${activeSegment.centerPct}%` }}
          className="pointer-events-auto absolute bottom-full z-30 mb-1 -translate-x-1/2 rounded-lg bg-[#0B1120] px-2.5 py-1.5 text-[11px] font-medium whitespace-nowrap text-white shadow-md tabular-nums"
        >
          {activeSegment.tooltipText}
        </div>
      )}

      <div className="flex h-2.5 w-full min-w-36 gap-[1px] overflow-hidden rounded-full bg-slate-100">
        {visualSegments.map((seg, idx) => (
          <button
            key={seg.id}
            type="button"
            aria-label={seg.ariaLabel}
            onMouseEnter={() => openTooltip(idx)}
            onMouseLeave={scheduleClose}
            onFocus={() => openTooltip(idx)}
            onBlur={scheduleClose}
            style={{ width: `${seg.widthPct}%` }}
            className={cn(
              'h-full shrink-0 cursor-pointer transition-opacity focus-visible:ring-2 focus-visible:ring-[#2563EB] focus-visible:outline-none',
              seg.kind === 'work' && 'bg-blue-600 hover:opacity-85',
              seg.kind === 'overtime' && 'bg-rose-500 hover:opacity-85',
              seg.kind === 'break' && 'bg-amber-400 hover:opacity-85',
            )}
          />
        ))}
      </div>
    </div>
  )
}
