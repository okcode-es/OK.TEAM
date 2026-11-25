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
