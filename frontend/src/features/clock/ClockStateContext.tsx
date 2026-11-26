import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { apiClient, extractApiError } from '@/api/client'
import type {
  AttendanceEventType,
  EmployeeClockStateResponse,
  MyDashboardEmployeeSummaryResponse,
  MyDashboardResponse,
  WorkMode,
} from '@/api/types'
import { useAuth } from '@/features/auth/AuthContext'

interface ClockStateContextValue {
  loading: boolean
  clockBusy: boolean
  error: string | null
  clearError: () => void
  clockVersion: number
  hasEmployeeProfile: boolean
  employee: MyDashboardEmployeeSummaryResponse | null
  clockState: EmployeeClockStateResponse | null
  myDashboard: MyDashboardResponse | null
  workMode: WorkMode
  setWorkMode: (mode: WorkMode) => void
  liveWorkedMinutes: number
  liveBreakMinutes: number
  autoBreakRemainingSeconds: number | null
  autoBreakCompleted: boolean
  clearAutoBreakCompleted: () => void
  refreshClockState: () => Promise<void>
  performClockAction: (
    eventType: Exclude<AttendanceEventType, 'correction'>,
    occurredAtUtc?: string,
  ) => Promise<boolean>
}

const ClockStateContext = createContext<ClockStateContextValue | null>(null)

const SYNC_THROTTLE_MS = 3000
const ERROR_AUTO_CLEAR_MS = 5000

export function ClockStateProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, activeTenant } = useAuth()
  const [loading, setLoading] = useState(false)
  const [clockBusy, setClockBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [clockVersion, setClockVersion] = useState(0)
  const [myDashboard, setMyDashboard] = useState<MyDashboardResponse | null>(
    null,
  )
  const [fetchedAtMs, setFetchedAtMs] = useState<number>(() => Date.now())
  const [nowMs, setNowMs] = useState<number>(() => Date.now())
  const [workMode, setWorkMode] = useState<WorkMode>('onsite')
  const [autoBreakCompleted, setAutoBreakCompleted] = useState(false)

  const lastSyncMsRef = useRef<number>(0)
  const errorTimerRef = useRef<number | null>(null)
  const autoBreakTriggeredRef = useRef<string | null>(null)

  const clearError = useCallback(() => {
    if (errorTimerRef.current !== null) {
      window.clearTimeout(errorTimerRef.current)
      errorTimerRef.current = null
    }
    setError(null)
  }, [])

  const showTransientError = useCallback((message: string) => {
    if (errorTimerRef.current !== null) {
      window.clearTimeout(errorTimerRef.current)
    }
    setError(message)
    errorTimerRef.current = window.setTimeout(() => {
      setError(null)
      errorTimerRef.current = null
    }, ERROR_AUTO_CLEAR_MS)
  }, [])

  useEffect(() => {
    return () => {
      if (errorTimerRef.current !== null) {
        window.clearTimeout(errorTimerRef.current)
      }
    }
  }, [])

  const syncClockStateInternal = useCallback(
    async (options?: { preserveError?: boolean }) => {
      if (!isAuthenticated || !activeTenant) {
        setMyDashboard(null)
        return
      }
      setLoading(true)
      if (!options?.preserveError) {
        clearError()
      }
      try {
        const { data, error: apiErr } = await apiClient.GET(
          '/api/v1/dashboard/me',
        )
        if (apiErr || !data) {
          if (!options?.preserveError) {
            showTransientError(extractApiError(apiErr))
          }
          return
        }
        lastSyncMsRef.current = Date.now()
        setMyDashboard(data)
        setFetchedAtMs(Date.now())
        setNowMs(Date.now())
        if (data.clock_state?.work_mode) {
          setWorkMode(data.clock_state.work_mode)
        }
      } catch (err) {
        if (!options?.preserveError) {
          showTransientError(extractApiError(err))
        }
      } finally {
        setLoading(false)
      }
    },
    [isAuthenticated, activeTenant, clearError, showTransientError],
  )

  const refreshClockState = useCallback(async () => {
    await syncClockStateInternal()
  }, [syncClockStateInternal])

  useEffect(() => {
    void refreshClockState()
  }, [refreshClockState, activeTenant?.tenant_id])

  // Background sync on window focus / tab visibility so multi-tab or mobile+desktop stays fresh
  useEffect(() => {
    if (!isAuthenticated || !activeTenant) return

    const maybeSync = () => {
      const now = Date.now()
      if (now - lastSyncMsRef.current < SYNC_THROTTLE_MS) return
      lastSyncMsRef.current = now
      void syncClockStateInternal({ preserveError: true })
    }

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        maybeSync()
      }
    }

    window.addEventListener('focus', maybeSync)
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      window.removeEventListener('focus', maybeSync)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [isAuthenticated, activeTenant, syncClockStateInternal])

  const clockStatus = myDashboard?.clock_state?.status ?? 'not_clocked_in'
  const isWorkingStatus = clockStatus === 'working' || clockStatus === 'remote'
  const isOnBreakStatus = clockStatus === 'on_break'
  const isTimerActive = isWorkingStatus || isOnBreakStatus

  useEffect(() => {
    if (!isTimerActive) return
    const timer = window.setInterval(() => {
      setNowMs(Date.now())
    }, 1000)
    return () => window.clearInterval(timer)
  }, [isTimerActive])

  const liveWorkedMinutes = useMemo(() => {
    const base = myDashboard?.clock_state?.worked_minutes_today ?? 0
    if (!isWorkingStatus) return base
    const elapsedMin = Math.max(0, Math.floor((nowMs - fetchedAtMs) / 60000))
    return base + elapsedMin
  }, [
    myDashboard?.clock_state?.worked_minutes_today,
    isWorkingStatus,
    nowMs,
    fetchedAtMs,
  ])

  const liveBreakMinutes = useMemo(() => {
    const base = myDashboard?.clock_state?.break_minutes_today ?? 0
    if (!isOnBreakStatus) return base
    const elapsedMin = Math.max(0, Math.floor((nowMs - fetchedAtMs) / 60000))
    return base + elapsedMin
  }, [
    myDashboard?.clock_state?.break_minutes_today,
    isOnBreakStatus,
    nowMs,
    fetchedAtMs,
  ])

  const autoBreakEndUtc = myDashboard?.clock_state?.auto_break_end_utc ?? null

  const autoBreakRemainingSeconds = useMemo(() => {
    if (!isOnBreakStatus || !autoBreakEndUtc) return null
    const endMs = new Date(autoBreakEndUtc).getTime()
    if (Number.isNaN(endMs)) return null
    return Math.max(0, Math.ceil((endMs - nowMs) / 1000))
  }, [isOnBreakStatus, autoBreakEndUtc, nowMs])

  useEffect(() => {
    if (
      isOnBreakStatus &&
      autoBreakEndUtc &&
      autoBreakRemainingSeconds === 0 &&
      autoBreakTriggeredRef.current !== autoBreakEndUtc
    ) {
      autoBreakTriggeredRef.current = autoBreakEndUtc
      setAutoBreakCompleted(true)
      void refreshClockState()
    }
  }, [
    isOnBreakStatus,
    autoBreakEndUtc,
    autoBreakRemainingSeconds,
    refreshClockState,
  ])

  const clearAutoBreakCompleted = useCallback(() => {
    setAutoBreakCompleted(false)
  }, [])

  const performClockAction = useCallback(
    async (
      eventType: Exclude<AttendanceEventType, 'correction'>,
      occurredAtUtc?: string,
    ): Promise<boolean> => {
      if (!myDashboard?.employee) return false
      setClockBusy(true)
      clearError()
      try {
        const { error: apiErr } = await apiClient.POST(
          '/api/v1/attendance/clock',
          {
            body: {
              event_type: eventType,
              source: 'web',
              store_id: myDashboard.employee.store_id,
              work_mode: workMode,
              occurred_at_utc: occurredAtUtc ?? null,
            },
          },
        )
        if (apiErr) {
          showTransientError(extractApiError(apiErr))
          await syncClockStateInternal({ preserveError: true })
          return false
        }
        await syncClockStateInternal()
        setClockVersion((v) => v + 1)
        return true
      } catch (err) {
        showTransientError(extractApiError(err))
        await syncClockStateInternal({ preserveError: true })
        return false
      } finally {
        setClockBusy(false)
      }
    },
    [
      myDashboard?.employee,
      workMode,
      clearError,
      showTransientError,
      syncClockStateInternal,
    ],
  )

  const value = useMemo<ClockStateContextValue>(
    () => ({
      loading,
      clockBusy,
      error,
      clearError,
      clockVersion,
      hasEmployeeProfile: Boolean(myDashboard?.has_employee_profile),
      employee: myDashboard?.employee ?? null,
      clockState: myDashboard?.clock_state ?? null,
      myDashboard,
      workMode,
      setWorkMode,
      liveWorkedMinutes,
      liveBreakMinutes,
      autoBreakRemainingSeconds,
      autoBreakCompleted,
      clearAutoBreakCompleted,
      refreshClockState,
      performClockAction,
    }),
    [
      loading,
      clockBusy,
      error,
      clearError,
      clockVersion,
      myDashboard,
      workMode,
      liveWorkedMinutes,
      liveBreakMinutes,
      autoBreakRemainingSeconds,
      autoBreakCompleted,
      clearAutoBreakCompleted,
      refreshClockState,
      performClockAction,
    ],
  )

  return (
    <ClockStateContext.Provider value={value}>
      {children}
    </ClockStateContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useClockState(): ClockStateContextValue {
  const ctx = useContext(ClockStateContext)
  if (!ctx) {
    throw new Error('useClockState must be used inside ClockStateProvider')
  }
  return ctx
}
