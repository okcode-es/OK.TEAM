import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  apiClient,
  extractApiError,
  extractFetchError,
  getApiBaseUrl,
  setActiveTenantHeader,
  setDevAccessToken,
} from '@/api/client'
import type { AuthMeResponse, TenantMembershipSummary } from '@/api/types'
import { clearKioskLock } from '@/features/attendance/kioskLock'
import { getSupabaseAuth } from '@/lib/supabase'

const STORAGE_DEV_TOKEN = 'okteam.dev_token'
const STORAGE_TENANT_ID = 'okteam.active_tenant_id'

interface AuthContextValue {
  isLoading: boolean
  isAuthenticated: boolean
  profile: AuthMeResponse | null
  activeTenant: TenantMembershipSummary | null
  devAuthAllowed: boolean
  signInWithSupabase: (email: string, password: string) => Promise<void>
  signInWithDevEmail: (email: string) => Promise<void>
  selectTenant: (tenantId: string) => void
  refreshProfile: (preferredTenantId?: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true)
  const [hasToken, setHasToken] = useState(false)
  const [profile, setProfile] = useState<AuthMeResponse | null>(null)
  const [activeTenantId, setActiveTenantId] = useState<string | null>(() =>
    localStorage.getItem(STORAGE_TENANT_ID),
  )

  const devAuthAllowed =
    import.meta.env.DEV || import.meta.env.VITE_DEV_AUTH_ENABLED === 'true'

  const selectTenant = useCallback((tenantId: string) => {
    setActiveTenantId(tenantId)
    setActiveTenantHeader(tenantId)
    localStorage.setItem(STORAGE_TENANT_ID, tenantId)
  }, [])

  const refreshProfile = useCallback(
    async (preferredTenantId?: string) => {
      const { data, error } = await apiClient.GET('/api/v1/auth/me')
      if (error || !data) {
        setProfile(null)
        setHasToken(false)
        setActiveTenantHeader(null)
        throw new Error(extractApiError(error, 'Failed to load profile'))
      }

      setProfile(data)
      setHasToken(true)

      const targetTenantId =
        preferredTenantId ??
        (activeTenantId &&
        data.tenants.some((item) => item.tenant_id === activeTenantId)
          ? activeTenantId
          : (data.tenants[0]?.tenant_id ?? null))

      if (targetTenantId) {
        selectTenant(targetTenantId)
      } else {
        setActiveTenantId(null)
        setActiveTenantHeader(null)
        localStorage.removeItem(STORAGE_TENANT_ID)
      }
    },
    [activeTenantId, selectTenant],
  )

  useEffect(() => {
    let cancelled = false

    async function bootstrap() {
      try {
        const storedDevToken = devAuthAllowed
          ? sessionStorage.getItem(STORAGE_DEV_TOKEN)
          : null

        if (storedDevToken) {
          setDevAccessToken(storedDevToken)
          await refreshProfile()
          return
        }

        const auth = getSupabaseAuth()
        if (auth) {
          const { data } = await auth.getSession()
          if (data.session?.access_token) {
            await refreshProfile()
            return
          }
        }
      } catch {
        sessionStorage.removeItem(STORAGE_DEV_TOKEN)
        setDevAccessToken(null)
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    void bootstrap()
    return () => {
      cancelled = true
    }
  }, [devAuthAllowed, refreshProfile])

  const signInWithSupabase = useCallback(
    async (email: string, password: string) => {
      const auth = getSupabaseAuth()
      if (!auth) {
        throw new Error('Supabase Auth is not configured')
      }
      setDevAccessToken(null)
      sessionStorage.removeItem(STORAGE_DEV_TOKEN)
      const { error } = await auth.signInWithPassword({ email, password })
      if (error) {
        throw new Error(error.message)
      }
      await refreshProfile()
    },
    [refreshProfile],
  )

  const signInWithDevEmail = useCallback(
    async (email: string) => {
      try {
        const response = await fetch(`${getApiBaseUrl()}/api/v1/dev/token`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        })
        if (!response.ok) {
          throw new Error(await extractFetchError(response))
        }
        const payload = (await response.json()) as { access_token: string }
        sessionStorage.setItem(STORAGE_DEV_TOKEN, payload.access_token)
        setDevAccessToken(payload.access_token)
        await refreshProfile()
      } catch (err) {
        throw new Error(extractApiError(err))
      }
    },
    [refreshProfile],
  )

  const signOut = useCallback(async () => {
    sessionStorage.removeItem(STORAGE_DEV_TOKEN)
    localStorage.removeItem(STORAGE_TENANT_ID)
    clearKioskLock()
    setDevAccessToken(null)
    setActiveTenantHeader(null)
    setProfile(null)
    setActiveTenantId(null)
    setHasToken(false)
    const auth = getSupabaseAuth()
    if (auth) {
      await auth.signOut()
    }
  }, [])

  const activeTenant = useMemo(() => {
    if (!profile || !activeTenantId) {
      return null
    }
    return (
      profile.tenants.find((item) => item.tenant_id === activeTenantId) ?? null
    )
  }, [profile, activeTenantId])

  const value = useMemo<AuthContextValue>(
    () => ({
      isLoading,
      isAuthenticated: hasToken && profile !== null,
      profile,
      activeTenant,
      devAuthAllowed,
      signInWithSupabase,
      signInWithDevEmail,
      selectTenant,
      refreshProfile,
      signOut,
    }),
    [
      isLoading,
      hasToken,
      profile,
      activeTenant,
      devAuthAllowed,
      signInWithSupabase,
      signInWithDevEmail,
      selectTenant,
      refreshProfile,
      signOut,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used inside AuthProvider')
  }
  return ctx
}
