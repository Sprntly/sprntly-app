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
import { setAccessTokenProvider } from "./api"
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
}

type AuthCtx = AuthState & {
  signInWithPassword: (email: string, password: string) => Promise<void>
  signUpWithPassword: (input: SignUpInput) => Promise<SignUpResult>
  resetPassword: (email: string) => Promise<void>
  resendVerificationEmail: (email: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
  postLoginPath: () => Promise<string>
  isEmailVerified: () => boolean
}

const Ctx = createContext<AuthCtx | null>(null)

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
    setState(sessionToState(session))
  }, [])

  useEffect(() => {
    if (!isSupabaseConfigured()) {
      setState({ kind: "unconfigured" })
      return
    }

    const supabase = getSupabase()

    setAccessTokenProvider(async () => {
      const {
        data: { session },
      } = await supabase.auth.getSession()
      return session?.access_token ?? null
    })

    void refresh()

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setState(sessionToState(session))
    })

    return () => subscription.unsubscribe()
  }, [refresh])

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    })
    if (error) throw error
  }, [])

  const signUpWithPassword = useCallback(
    async (input: SignUpInput): Promise<SignUpResult> => {
      const supabase = getSupabase()
      const { data, error } = await supabase.auth.signUp({
        email: input.email.trim(),
        password: input.password,
        options: {
          emailRedirectTo: authCallbackUrl(),
          data: {
            first_name: input.firstName.trim(),
            last_name: input.lastName.trim(),
            ...(input.role?.trim() ? { role: input.role.trim() } : {}),
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
    const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
      redirectTo: authCallbackUrl(),
    })
    if (error) throw error
  }, [])

  const resendVerificationEmail = useCallback(async (email: string) => {
    const supabase = getSupabase()
    const { error } = await supabase.auth.resend({
      type: "signup",
      email: email.trim(),
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
