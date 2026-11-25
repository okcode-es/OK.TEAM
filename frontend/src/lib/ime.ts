import type React from 'react'

/**
 * Prevent accidental form submit / search trigger while composing Chinese/CJK IME input.
 */
export function isImeComposing(event: React.KeyboardEvent): boolean {
  const native = event.nativeEvent as KeyboardEvent & { keyCode?: number }
  return Boolean(native.isComposing || native.keyCode === 229)
}

export const SPAIN_TIMEZONES = [
  'Europe/Madrid',
  'Atlantic/Canary',
  'Africa/Ceuta',
  'UTC',
] as const

export function formatUtcDateTime(
  isoUtc: string,
  locale: string,
  timeZone: string = 'Europe/Madrid',
): string {
  const parsed = new Date(isoUtc)
  if (Number.isNaN(parsed.getTime())) {
    return isoUtc
  }
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone,
  }).format(parsed)
}

/**
 * Convert a wall-clock date (`YYYY-MM-DD`) and time (`HH:mm`) in `timeZone`
 * to a UTC ISO-8601 string, handling DST transitions accurately.
 */
export function localDateAndTimeInZoneToUtcIso(
  localDate: string,
  localTime: string,
  timeZone: string,
): string {
  const [year, month, day] = localDate.split('-').map(Number)
  const [hour, minute] = localTime.split(':').map(Number)
  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(hour) ||
    !Number.isFinite(minute)
  ) {
    return new Date().toISOString()
  }

  const naiveUtcMs = Date.UTC(year, month - 1, day, hour, minute, 0)
  const partsFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  const getOffsetMs = (utcMs: number): number => {
    const parts = partsFormatter.formatToParts(new Date(utcMs))
    const map: Record<string, number> = {}
    for (const part of parts) {
      if (part.type !== 'literal') {
        map[part.type] = Number(part.value)
      }
    }
    const asUtc = Date.UTC(
      map.year,
      map.month - 1,
      map.day,
      map.hour,
      map.minute,
      map.second,
    )
    return asUtc - utcMs
  }

  const firstPass = naiveUtcMs - getOffsetMs(naiveUtcMs)
  const secondPass = naiveUtcMs - getOffsetMs(firstPass)
  return new Date(secondPass).toISOString()
}

/**
 * Extract `YYYY-MM-DD` and `HH:mm` in `timeZone` from a UTC ISO-8601 string.
 */
export function utcIsoToLocalDateAndTime(
  isoUtc: string,
  timeZone: string,
): { date: string; time: string } {
  const parsed = new Date(isoUtc)
  if (Number.isNaN(parsed.getTime())) {
    return { date: '', time: '09:00' }
  }
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(parsed)

  const map: Record<string, string> = {}
  for (const part of parts) {
    if (part.type !== 'literal') {
      map[part.type] = part.value
    }
  }
  return {
    date: `${map.year}-${map.month}-${map.day}`,
    time: `${map.hour}:${map.minute}`,
  }
}
