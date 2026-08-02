"use client"

// A guest-viewer session — set by GuestArtifactViewer around its OWN subtree
// only (never by ArtifactShareGate around `children`, since `children` — the
// real WorkspaceProvider > ... > AppShell tree — never renders for a guest).
//
// Unlike every other app-level context (useNavigation/useContent/useWorkspace),
// this hook does NOT throw when there is no provider ancestor — it returns
// null. That's load-bearing: ContentPanel and PrdPanelContent call
// useGuestSession() unconditionally so their existing (non-guest) render path
// stays provably unchanged — every pre-existing ContentPanel/PrdPanelContent
// test mounts those components with no GuestSessionProvider in the tree at
// all, and must keep passing with zero modification.
import { createContext, useContext, type ReactNode } from "react"

export type GuestSession = {
  /** The artifact-share token this session was resolved from, or null for a
   *  bare `?prd=` link resolved with no token (company-domain match only —
   *  see prd_access.py). */
  token: string | null
  /** The PRD's opaque, unguessable external identifier — set only in
   *  token-less (bare-link) mode; null in token mode (the share token is the
   *  external identifier there). Never the raw sequential prd id. */
  publicId: string | null
  /** Null for a token-less session — there is no share row's sharer to
   *  attribute a bare-link view to. */
  sharerName: string | null
  owningCompanyName: string
  artifactId: number
}

const GuestSessionContext = createContext<GuestSession | null>(null)

export function GuestSessionProvider({
  value,
  children,
}: {
  value: GuestSession
  children: ReactNode
}) {
  return (
    <GuestSessionContext.Provider value={value}>
      {children}
    </GuestSessionContext.Provider>
  )
}

/** Null outside a GuestArtifactViewer subtree — this is the normal, expected
 *  case for every authed-member render path. */
export function useGuestSession(): GuestSession | null {
  return useContext(GuestSessionContext)
}
