import { createClient, type SupabaseClient } from "@supabase/supabase-js"
import { fetchWorkspaceForUser } from "../onboarding/store"

let browserClient: SupabaseClient | null = null

function trimEnv(value: string | undefined): string {
  return (value ?? "").trim()
}

/** Must be https://<project-ref>.supabase.co (no trailing slash). */
export function parseSupabaseUrl(raw: string | undefined): string | null {
  const value = trimEnv(raw)
  if (!value) return null
  try {
    const parsed = new URL(value)
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null
    if (!parsed.hostname) return null
    // Supabase project URL is origin-only; reject stray paths/query.
    if (parsed.pathname !== "/" && parsed.pathname !== "") return null
    if (parsed.search || parsed.hash) return null
    return parsed.origin
  } catch {
    return null
  }
}

export function getSupabasePublicConfig(): {
  url: string
  anonKey: string
} | null {
  const url = parseSupabaseUrl(process.env.NEXT_PUBLIC_SUPABASE_URL)
  const anonKey = trimEnv(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY)
  if (!url || !anonKey) return null
  return { url, anonKey }
}

export function isSupabaseConfigured(): boolean {
  return getSupabasePublicConfig() !== null
}

export function getSupabase(): SupabaseClient {
  if (browserClient) return browserClient

  const config = getSupabasePublicConfig()
  if (!config) {
    throw new Error(
      "Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL (https://YOUR_REF.supabase.co) and NEXT_PUBLIC_SUPABASE_ANON_KEY at build time, then redeploy.",
    )
  }

  browserClient = createClient(config.url, config.anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  })
  return browserClient
}

/** Email-confirmation redirect (static export — client-side callback). */
export function authCallbackUrl(): string {
  if (typeof window === "undefined") return "/auth/callback"
  return `${window.location.origin}/auth/callback`
}

/** Where to send the user after a successful sign-in. */
export async function postLoginPath(): Promise<string> {
  const supabase = getSupabase()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return "/sign-in"

  if (!user.email_confirmed_at) return "/verify-email"

  const workspace = await fetchWorkspaceForUser(user.id)

  // Auto-accept-on-sign-in (CEO 2-A): if the user has no workspace yet,
  // check the backend for a pending invite that matches their verified
  // email. On success the backend creates their company_members row, so
  // the next workspace fetch resolves to a real company. Best-effort —
  // any failure (404 = no invite, 409 = already in another company,
  // network glitch) falls through to onboarding without surfacing an
  // error here.
  if (!workspace) {
    const accepted = await tryAutoAcceptInvite()
    if (accepted) {
      const fresh = await fetchWorkspaceForUser(user.id)
      if (fresh) {
        if (fresh.onboarding_completed_at) return "/"
        const step = Math.min(Math.max(fresh.onboarding_step, 1), 8)
        return `/onboarding/${step}`
      }
    }
    return "/onboarding/1"
  }
  if (workspace.onboarding_completed_at) return "/"
  const step = Math.min(Math.max(workspace.onboarding_step, 1), 8)
  return `/onboarding/${step}`
}

async function tryAutoAcceptInvite(): Promise<boolean> {
  try {
    // Lazy import keeps the team-settings module (which transitively pulls
    // in React-flavoured deps) out of the cold-start path of postLoginPath.
    const { teamApi } = await import(
      "../../components/screens/app/settings/TeamSettings"
    )
    await teamApi.acceptInvite()
    return true
  } catch {
    return false
  }
}
