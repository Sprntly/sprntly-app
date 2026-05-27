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

export type SignUpResult = "session" | "confirm_email"

type AuthCtx = AuthState & {
  signInWithPassword: (email: string, password: string) => Promise<void>
  signUpWithPassword: (email: string, password: string) => Promise<SignUpResult>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
  postLoginPath: () => Promise<string>
}

const Ctx = createContext<AuthCtx | null>(null)

function sessionToState(session: Session | null): AuthState {
  if (!session?.user) return { kind: "anonymous" }
  return { kind: "authed", user: session.user, session }
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
    async (email: string, password: string): Promise<SignUpResult> => {
      const supabase = getSupabase()
      const { data, error } = await supabase.auth.signUp({
        email: email.trim(),
        password,
      })
      if (error) throw error
      return data.session ? "session" : "confirm_email"
    },
    [],
  )

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

  const value = useMemo<AuthCtx>(
    () => ({
      ...state,
      signInWithPassword,
      signUpWithPassword,
      signOut,
      refresh,
      postLoginPath,
    }),
    [state, signInWithPassword, signUpWithPassword, signOut, refresh],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
