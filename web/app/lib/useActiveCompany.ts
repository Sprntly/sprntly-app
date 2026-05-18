"use client"

import { useCallback, useEffect, useState } from "react"

const LS_KEY = "sprntly_active_company"
const DEFAULT_SLUG = "asurion"

/**
 * Active-company state for the demo. Resolution order:
 *   1. ?company=… URL query
 *   2. localStorage["sprntly_active_company"]
 *   3. "asurion" (back-compat with the original single-company demo)
 *
 * Writes back to both localStorage and the URL so reload + share-the-link
 * both work. URL changes via history.replaceState; we never reload.
 */
export function resolveInitialCompany(
  search: string | null,
  storage: Storage | null,
): string {
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
  return DEFAULT_SLUG
}

export function useActiveCompany(): [string, (slug: string) => void] {
  const [slug, setSlugState] = useState<string>(() => {
    if (typeof window === "undefined") return DEFAULT_SLUG
    return resolveInitialCompany(window.location.search, window.localStorage)
  })

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
