import i18n from '@/i18n'

const DETAIL_PATTERNS: Array<[RegExp, string]> = [
  [/cannot clock_in while shift is already open|shift is already open/i, 'ATTENDANCE_SHIFT_ALREADY_OPEN'],
  [/without an open shift|no open shift/i, 'ATTENDANCE_NO_OPEN_SHIFT'],
  [/break is already open/i, 'ATTENDANCE_BREAK_ALREADY_OPEN'],
  [/no open break/i, 'ATTENDANCE_NO_OPEN_BREAK'],
  [/break.*not ended|end.*break.*first/i, 'ATTENDANCE_BREAK_NOT_ENDED'],
  [/chronolog|occurred_at.*before|precedes/i, 'ATTENDANCE_CHRONOLOGY_ERROR'],
  [/invalid.*time.*range|clock_in.*before|break.*between/i, 'ATTENDANCE_INVALID_TIME_RANGE'],
  [/session.*overlap|overlapping.*shift|already.*open.*shift/i, 'ATTENDANCE_SESSION_OVERLAP'],
  [/requires.*manager.*first|pending_manager/i, 'LEAVE_REQUIRES_MANAGER_FIRST'],
  [/requires.*hr.*final|pending_hr/i, 'LEAVE_REQUIRES_HR_FINAL'],
  [/payroll.*closed|month.*closed/i, 'PAYROLL_MONTH_CLOSED'],
  [/payroll.*conflict|closing.*already/i, 'PAYROLL_CONFLICT'],
  [/retention.*lock|4-year retention/i, 'RETENTION_LOCK'],
  [/frozen|anonymized/i, 'FROZEN_RECORD'],
  [/leave.*overlap|overlapping leave/i, 'LEAVE_OVERLAP'],
  [/insufficient.*balance|vacation balance/i, 'LEAVE_INSUFFICIENT_BALANCE'],
  [/shift.*conflict|overlapping shift/i, 'SHIFT_CONFLICT'],
  [/invalid.*file.*type|unsupported.*media|mime/i, 'FILE_INVALID_TYPE'],
  [/file.*too large|exceeds.*size/i, 'FILE_TOO_LARGE'],
  [/import.*invalid.*file|corrupt.*file/i, 'IMPORT_INVALID_FILE'],
  [/\bnie\b|\bdni\b/i, 'INVALID_NIE_DNI'],
  [/\biban\b/i, 'INVALID_IBAN'],
  [/\bnaf\b/i, 'INVALID_NAF'],
  [/already exists|duplicate/i, 'DUPLICATE_RECORD'],
  [/invalid.*token|expired.*token|unauthorized/i, 'AUTH_INVALID_TOKEN'],
  [/forbidden|permission|not allowed/i, 'FORBIDDEN'],
  [/signed url/i, 'SIGNED_URL_INVALID'],
  [/not found/i, 'NOT_FOUND'],
  [/last owner/i, 'LAST_OWNER_PROTECTED'],
  [/conflict/i, 'CONFLICT'],
  [/validation/i, 'VALIDATION_ERROR'],
  [/\bpii\b/i, 'PII_SERVICE_ERROR'],
  [/storage/i, 'STORAGE_SERVICE_ERROR'],
  [/internal server error/i, 'INTERNAL_ERROR'],
]

function translateCode(code: string): string | null {
  const key = `errors.${code}`
  if (i18n.exists(key)) {
    return i18n.t(key)
  }
  return null
}

function classifyHttpStatus(status: number): string {
  if (status === 401) return 'HTTP_401'
  if (status === 403) return 'HTTP_403'
  if (status === 404) return 'HTTP_404'
  if (status === 405) return 'HTTP_405'
  if (status === 422) return 'HTTP_422'
  return 'HTTP_ERROR'
}

function isNetworkError(error: unknown): boolean {
  if (error instanceof TypeError) {
    return true
  }
  const msg =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : ''
  return /failed to fetch|networkerror|load failed|network request failed|err_connection|econnrefused/i.test(
    msg,
  )
}

function extractRawDetail(error: unknown): string {
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: unknown } | undefined
      if (first && typeof first.msg === 'string') return first.msg
    }
  }
  return ''
}

export function translateApiError(error: unknown, fallback?: string): string {
  const genericFallback = fallback || i18n.t('errors.GENERIC')

  if (error && typeof error === 'object') {
    const obj = error as {
      error_code?: unknown
      code?: unknown
      status?: unknown
    }

    if (typeof obj.error_code === 'string' && obj.error_code.trim()) {
      const translated = translateCode(obj.error_code.trim())
      if (translated) return translated
    }

    if (typeof obj.code === 'string' && obj.code.trim()) {
      const translated = translateCode(obj.code.trim())
      if (translated) return translated
    }

    if (isNetworkError(error)) {
      return i18n.t('errors.NETWORK_ERROR')
    }

    const detail = extractRawDetail(error)
    if (detail) {
      for (const [pattern, code] of DETAIL_PATTERNS) {
        if (pattern.test(detail)) {
          const translated = translateCode(code)
          if (translated) return translated
        }
      }
    }

    if (typeof obj.status === 'number' && obj.status >= 400) {
      const httpCode = classifyHttpStatus(obj.status)
      const translated = translateCode(httpCode)
      if (translated) return translated
    }
  } else {
    if (isNetworkError(error)) {
      return i18n.t('errors.NETWORK_ERROR')
    }
    const detail = extractRawDetail(error)
    if (detail) {
      for (const [pattern, code] of DETAIL_PATTERNS) {
        if (pattern.test(detail)) {
          const translated = translateCode(code)
          if (translated) return translated
        }
      }
    }
  }

  console.warn('[API Error]', error)
  return genericFallback
}

export async function extractFetchError(
  response: Response,
  fallback?: string,
): Promise<string> {
  const payload = (await response.json().catch(() => null)) as Record<
    string,
    unknown
  > | null
  if (payload && typeof payload === 'object') {
    return translateApiError({ status: response.status, ...payload }, fallback)
  }
  return translateApiError({ status: response.status }, fallback)
}
