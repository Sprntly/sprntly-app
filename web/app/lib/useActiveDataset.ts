"use client"

import { useCallback, useEffect, useState } from "react"

const LS_KEY = "sprntly_active_dataset"
const DEFAULT_SLUG = "asurion"

/**
 * Active-dataset state for the demo. Resolution order:
 *   1. ?dataset=… URL query
 *   2. localStorage["sprntly_active_dataset"]
 *   3. "asurion" (back-compat with the original single-dataset demo)
 *
 * Writes back to both localStorage and the URL so reload + share-the-link
 * both work. URL changes via history.replaceState; we never reload.
 */
export function resolveInitialDataset(
  search: string | null,
  storage: Storage | null,
): string {
  if (search) {
    try {
      const v = new URLSearchParams(search).get("dataset")
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

export function useActiveDataset(): [string, (slug: string) => void] {
  const [slug, setSlugState] = useState<string>(() => {
    if (typeof window === "undefined") return DEFAULT_SLUG
    return resolveInitialDataset(window.location.search, window.localStorage)
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
    if (url.searchParams.get("dataset") !== slug) {
      url.searchParams.set("dataset", slug)
      window.history.replaceState({}, "", url.toString())
    }
  }, [slug])

  const setSlug = useCallback((next: string) => {
    setSlugState(next)
  }, [])

  return [slug, setSlug]
}
