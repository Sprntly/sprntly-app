"use client"

import { useCallback, useEffect, useState } from "react"

const LS_KEY = "sprntly_active_company"
export const DEMO_DEFAULT_COMPANY_SLUG = "asurion"

/**
 * Active dataset slug for brief/API calls. Resolution order:
 *   1. Signed-in workspace slug (app / Supabase)
 *   2. ?company=… URL query
 *   3. localStorage["sprntly_active_company"]
 *   4. Demo default ("asurion")
 */
export function resolveInitialCompany(
  search: string | null,
  storage: Storage | null,
  workspaceSlug?: string | null,
): string {
  if (workspaceSlug && workspaceSlug.length >= 2) return workspaceSlug
  if (search) {
    try {
      const v = new URLSearchParams(search).get("company")
      if (v && v.length >= 2) return v
    } catch {
      // bad query, fall through
    }
  }
  if (storage) {
    const v = storage.getItem(LS_KEY)
    if (v && v.length >= 2) return v
  }
  return DEMO_DEFAULT_COMPANY_SLUG
}

export function useActiveCompany(
  workspaceSlug?: string | null,
): [string, (slug: string) => void] {
  const [slug, setSlugState] = useState<string>(() => {
    if (typeof window === "undefined") {
      return workspaceSlug ?? DEMO_DEFAULT_COMPANY_SLUG
    }
    return resolveInitialCompany(
      window.location.search,
      window.localStorage,
      workspaceSlug,
    )
  })

  // When Supabase workspace loads, prefer it over a stale demo slug in storage.
  useEffect(() => {
    if (!workspaceSlug || workspaceSlug.length < 2) return
    setSlugState((prev) => (prev === workspaceSlug ? prev : workspaceSlug))
  }, [workspaceSlug])

  // Keep storage + URL in sync whenever slug changes.
  useEffect(() => {
    if (typeof window === "undefined") return
    try {
      window.localStorage.setItem(LS_KEY, slug)
    } catch {
      // localStorage may be disabled in some browsers; not fatal.
    }
    const url = new URL(window.location.href)
    if (url.searchParams.get("company") !== slug) {
      url.searchParams.set("company", slug)
      window.history.replaceState({}, "", url.toString())
    }
  }, [slug])

  const setSlug = useCallback((next: string) => {
    setSlugState(next)
  }, [])

  return [slug, setSlug]
}
