"use client"

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { auth as authApi, ApiError } from "./api"

type AuthState =
  | { kind: "loading" }
  | { kind: "authed" }
  | { kind: "anonymous" }

type AuthCtx = AuthState & {
  signIn: (password: string) => Promise<void>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

const Ctx = createContext<AuthCtx | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" })

  const refresh = useCallback(async () => {
    try {
      await authApi.me()
      setState({ kind: "authed" })
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setState({ kind: "anonymous" })
      } else {
        // network error etc — treat as anonymous so user can still try to sign in
        setState({ kind: "anonymous" })
      }
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signIn = useCallback(async (password: string) => {
    await authApi.login(password)
    setState({ kind: "authed" })
  }, [])

  const signOut = useCallback(async () => {
    try {
      await authApi.logout()
    } finally {
      setState({ kind: "anonymous" })
    }
  }, [])

  const value = useMemo<AuthCtx>(
    () => ({ ...state, signIn, signOut, refresh }),
    [state, signIn, signOut, refresh],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useAuth() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useAuth must be used within AuthProvider")
  return ctx
}
