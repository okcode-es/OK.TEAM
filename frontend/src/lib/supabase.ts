import { createClient, type SupabaseClient } from '@supabase/supabase-js'

/**
 * Security boundary (DATA_AND_SECURITY.md):
 * Frontend MUST ONLY use Supabase Auth for JWT acquisition.
 * NEVER query business tables (.from) or storage buckets (.storage) directly from the browser.
 */
let _supabaseClient: SupabaseClient | null = null

function getClient(): SupabaseClient | null {
  const url = import.meta.env.VITE_SUPABASE_URL
  const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY
  if (!url || !anonKey) {
    return null
  }
  if (!_supabaseClient) {
    _supabaseClient = createClient(url, anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  }
  return _supabaseClient
}

/**
 * Exposes ONLY the Supabase Auth namespace — structurally prevents direct DB/Storage access.
 */
export function getSupabaseAuth() {
  return getClient()?.auth ?? null
}

export async function getAccessToken(): Promise<string | null> {
  const auth = getSupabaseAuth()
  if (!auth) {
    return null
  }
  const { data } = await auth.getSession()
  return data.session?.access_token ?? null
}
