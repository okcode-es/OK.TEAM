import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from '@/api/generated/schema'
import { getAccessToken } from '@/lib/supabase'

let _devToken: string | null = null
let _activeTenantId: string | null = null

export function setDevAccessToken(token: string | null): void {
  _devToken = token
}

export function setActiveTenantHeader(tenantId: string | null): void {
  _activeTenantId = tenantId
}

export function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
}

export async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {}
  const token = _devToken ?? (await getAccessToken())
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (_activeTenantId) {
    headers['X-Tenant-Id'] = _activeTenantId
  }
  return headers
}

const authAndTenantMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = _devToken ?? (await getAccessToken())
    if (token) {
      request.headers.set('Authorization', `Bearer ${token}`)
    }
    if (_activeTenantId) {
      request.headers.set('X-Tenant-Id', _activeTenantId)
    }
    return request
  },
}

export const apiClient = createClient<paths>({
  baseUrl: getApiBaseUrl(),
})

apiClient.use(authAndTenantMiddleware)

export function extractApiError(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return fallback
}
