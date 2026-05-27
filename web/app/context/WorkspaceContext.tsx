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
import { fetchUserProfile, fetchWorkspaceForUser } from "../lib/onboarding/store"
import type { UserProfile, WorkspaceCompany } from "../lib/onboarding/types"
import { isSupabaseConfigured } from "../lib/supabase/client"

type WorkspaceCtx = {
  loading: boolean
  profile: UserProfile | null
  workspace: WorkspaceCompany | null
  refresh: () => Promise<void>
}

const Ctx = createContext<WorkspaceCtx | null>(null)

export function profileDisplayName(
  profile: UserProfile | null,
  fallbackEmail?: string | null,
): string | null {
  if (!profile) return null
  const full = [profile.first_name, profile.last_name]
    .map((s) => s?.trim())
    .filter(Boolean)
    .join(" ")
  if (full) return full
  if (fallbackEmail) {
    const local = fallbackEmail.split("@")[0]
    if (local) return local
  }
  return null
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const auth = useAuth()
  const [loading, setLoading] = useState(true)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [workspace, setWorkspace] = useState<WorkspaceCompany | null>(null)

  const refresh = useCallback(async () => {
    if (auth.kind !== "authed" || !isSupabaseConfigured()) {
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
    () => ({ loading, profile, workspace, refresh }),
    [loading, profile, workspace, refresh],
  )

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useWorkspace(): WorkspaceCtx {
  const ctx = useContext(Ctx)
  if (!ctx) {
    throw new Error("useWorkspace must be used within WorkspaceProvider")
  }
  return ctx
}
