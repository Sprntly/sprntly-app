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
import { useAuth } from "../lib/auth"
import {
  fetchUserProfile,
  fetchWorkspaceForUser,
} from "../lib/onboarding/store"
import type { UserProfile, WorkspaceCompany } from "../lib/onboarding/types"

type OnboardingCtx = {
  loading: boolean
  profile: UserProfile | null
  workspace: WorkspaceCompany | null
  refresh: () => Promise<void>
  setWorkspace: (w: WorkspaceCompany | null) => void
}

const Ctx = createContext<OnboardingCtx | null>(null)

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [loading, setLoading] = useState(true)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [workspace, setWorkspace] = useState<WorkspaceCompany | null>(null)

  const refresh = useCallback(async () => {
    if (auth.kind !== "authed") {
      setProfile(null)
      setWorkspace(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const [p, w] = await Promise.all([
        fetchUserProfile(auth.user.id),
        fetchWorkspaceForUser(auth.user.id),
      ])
      setProfile(p)
      setWorkspace(w)
    } finally {
      setLoading(false)
    }
  }, [auth])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo(
    () => ({ loading, profile, workspace, refresh, setWorkspace }),
    [loading, profile, workspace, refresh],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useOnboarding() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useOnboarding must be used within OnboardingProvider")
  return ctx
}
