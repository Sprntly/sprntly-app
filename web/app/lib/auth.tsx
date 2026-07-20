"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import type { Session, User } from "@supabase/supabase-js"
import { authCallbackUrl } from "./supabase/client"
import { normalizeEmail } from "./auth-validation"
import { setAccessTokenProvider } from "./api"
import { resetSettingsCaches } from "./settingsCache"
import {
  getSupabase,
  isSupabaseConfigured,
  postLoginPath,
} from "./supabase/client"

type AuthState =
  | { kind: "loading" }
  | { kind: "authed"; user: User; session: Session }
  | { kind: "anonymous" }
  | { kind: "unconfigured" }

export type SignUpResult = "session" | "confirm_email" | "already_registered"

/** Map a successful supabase.auth.signUp response to what the UI should do.
 *  Confirm-email mode returns "success" with an obfuscated user carrying NO
 *  identities when the address is already registered — and sends no email.
 *  Surface that instead of a false "check your inbox". */
export function interpretSignUpResponse(data: {
  user: { identities?: unknown[] | null } | null
  session: unknown | null
}): SignUpResult {
  if (data.user && (data.user.identities?.length ?? 0) === 0) {
    return "already_registered"
  }
  return data.session ? "session" : "confirm_email"
}

export type SignUpInput = {
  email: string
  password: string
  firstName: string
  lastName: string
  /** v4 page 03 "about you" — optional self-reported role. */
  role?: string
  /** "Your priorities — what you're focused on right now" (v6 about-you).
   *  Persisted to profiles.priorities by the handle_new_user trigger. */
  priorities?: string
  /** Legacy signup choice — the v6 flow always sends "company" (the
   *  company/personal split is retired from the UI). */
  accountType?: "company" | "personal"
  /** IANA timezone (e.g. "America/New_York"). Optional override; when absent we
   *  auto-detect from the browser so the weekly brief fires Monday 06:00 local. */
  timezone?: string
}

/** Best-effort IANA timezone of the current browser (e.g. "America/New_York").
 *  Returns undefined if the environment can't report one — the backend then
 *  falls back to UTC. */
export function detectBrowserTimezone(): string | undefined {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    return tz && tz.trim() ? tz.trim() : undefined
  } catch {
    return undefined
  }
}

type AuthCtx = AuthState & {
  signInWithPassword: (email: string, password: string) => Promise<void>
  signInWithGoogle: () => Promise<void>
  signUpWithPassword: (input: SignUpInput) => Promise<SignUpResult>
  resetPassword: (email: string) => Promise<void>
  resendVerificationEmail: (email: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
  postLoginPath: () => Promise<string>
  isEmailVerified: () => boolean
}

const Ctx = createContext<AuthCtx | null>(null)

// The last session observed from Supabase, kept current by refresh() and the
// onAuthStateChange subscription (and cleared on sign-out). Lets the
// accessTokenProvider hand out the current token WITHOUT calling
// supabase.auth.getSession() — which acquires a navigator.locks mutex on every
// call and serialized all concurrent API requests behind one LockManager
// queue. Module-level (not state) so the provider closure always reads the
// freshest value.
let cachedSession: Session | null = null

function sessionToState(session: Session | null): AuthState {
  if (!session?.user) return { kind: "anonymous" }
  return { kind: "authed", user: session.user, session }
}

export function isUserEmailVerified(user: User): boolean {
  return !!user.email_confirmed_at
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" })

  const refresh = useCallback(async () => {
    if (!isSupabaseConfigured()) {
      setState({ kind: "unconfigured" })
      return
    }
    const supabase = getSupabase()
    const {
      data: { session },
    } = await supabase.auth.getSession()
    cachedSession = session
    setState(sessionToState(session))
  }, [])

  useEffect(() => {
    if (!isSupabaseConfigured()) {
      setState({ kind: "unconfigured" })
      return
    }

    const supabase = getSupabase()

    setAccessTokenProvider(async () => {
      // Fast path: serve the cached token while it has >30s of life left, so
      // parallel API calls don't serialize behind getSession()'s LockManager
      // lock. Near/past expiry, fall through to getSession(), which refreshes
      // the token if needed.
      if (
        cachedSession?.access_token &&
        (cachedSession.expires_at ?? 0) * 1000 - Date.now() > 30_000
      ) {
        return cachedSession.access_token
      }
      const {
        data: { session },
      } = await supabase.auth.getSession()
      cachedSession = session
      return session?.access_token ?? null
    })

    void refresh()

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      cachedSession = session
      setState(sessionToState(session))
    })

    return () => subscription.unsubscribe()
  }, [refresh])

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.signInWithPassword({
      email: normalizeEmail(email),
      password,
    })
    if (error) throw error
  }, [])

  const signInWithGoogle = useCallback(async () => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: authCallbackUrl() },
    })
    if (error) throw error
  }, [])

  const signUpWithPassword = useCallback(
    async (input: SignUpInput): Promise<SignUpResult> => {
      const supabase = getSupabase()
      const timezone = input.timezone?.trim() || detectBrowserTimezone()
      const { data, error } = await supabase.auth.signUp({
        email: normalizeEmail(input.email),
        password: input.password,
        options: {
          emailRedirectTo: authCallbackUrl(),
          data: {
            first_name: input.firstName.trim(),
            last_name: input.lastName.trim(),
            ...(input.role?.trim() ? { role: input.role.trim() } : {}),
            ...(input.priorities?.trim() ? { priorities: input.priorities.trim() } : {}),
            ...(input.accountType ? { account_type: input.accountType } : {}),
            ...(timezone ? { timezone } : {}),
          },
        },
      })
      if (error) throw error
      return interpretSignUpResponse(data)
    },
    [],
  )

  const resetPassword = useCallback(async (email: string) => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.resetPasswordForEmail(normalizeEmail(email), {
      redirectTo: authCallbackUrl(),
    })
    if (error) throw error
  }, [])

  const resendVerificationEmail = useCallback(async (email: string) => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.resend({
      type: "signup",
      email: normalizeEmail(email),
      options: { emailRedirectTo: authCallbackUrl() },
    })
    if (error) throw error
  }, [])

  const signOut = useCallback(async () => {
    if (!isSupabaseConfigured()) {
      setState({ kind: "unconfigured" })
      return
    }
    const supabase = getSupabase()
    try {
      await supabase.auth.signOut()
    } finally {
      // Drop the token cache immediately — the next user must never inherit a
      // still-valid bearer from the previous session.
      cachedSession = null
      // Wipe all session-scoped storage so a different user logging in on the
      // same browser never sees the previous user's data (chat tabs, active
      // company, conversation resume, etc.). Chat tabs now live in
      // sessionStorage (session-scoped by design — see ChatScreen); we clear
      // BOTH storages: sessionStorage so a same-tab re-login starts fresh, and
      // localStorage to sweep any stale tab entries written before that move.
      try {
        // Fixed keys
        const SESSION_KEYS = [
          "sprntly_active_company",
          "sprntly_chat_tabs",
          "sprntly_chat_active_tab",
          "sprntly_resume_conv",
        ]
        for (const key of SESSION_KEYS) {
          localStorage.removeItem(key)
          sessionStorage.removeItem(key)
        }
        // Company-scoped keys (sprntly_chat_tabs_<slug>, etc.) across both stores.
        const isTabKey = (key: string | null): key is string =>
          !!key && (key.startsWith("sprntly_chat_tabs_") || key.startsWith("sprntly_chat_active_tab_"))
        for (const store of [localStorage, sessionStorage]) {
          const toRemove: string[] = []
          for (let i = 0; i < store.length; i++) {
            const key = store.key(i)
            if (isTabKey(key)) toRemove.push(key)
          }
          for (const key of toRemove) store.removeItem(key)
        }
      } catch {
        // storage may be disabled; not fatal.
      }
      // In-memory settings-pane caches (Connectors/MCP/Team/Admin) survive
      // localStorage wipes — clear them too so the next user never flashes the
      // previous account's connectors/tokens/team before revalidation.
      try {
        resetSettingsCaches()
      } catch {
        // Never let cache cleanup block sign-out.
      }
      setState({ kind: "anonymous" })
    }
  }, [])

  const isEmailVerified = useCallback(() => {
    if (state.kind !== "authed") return false
    return isUserEmailVerified(state.user)
  }, [state])

  const value = useMemo<AuthCtx>(
    () => ({
      ...state,
      signInWithPassword,
      signInWithGoogle,
      signUpWithPassword,
      resetPassword,
      resendVerificationEmail,
      signOut,
      refresh,
      postLoginPath,
      isEmailVerified,
    }),
    [
      state,
      signInWithPassword,
      signInWithGoogle,
      signUpWithPassword,
      resetPassword,
      resendVerificationEmail,
      signOut,
      refresh,
      isEmailVerified,
    ],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
