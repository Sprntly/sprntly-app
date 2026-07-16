"use client"

import { createContext, useContext, type ReactNode } from "react"
import { useActiveCompany, DEMO_DEFAULT_COMPANY_SLUG } from "../lib/useActiveCompany"
import { useWorkspace } from "./WorkspaceContext"

type Ctx = {
  activeCompany: string
  setActiveCompany: (slug: string) => void
  // Human-readable company name (companies.display_name), sourced from the
  // workspace. Distinct from `activeCompany` (the opaque dataset slug) — used to
  // build the cosmetic /p/<company>/<feature>/<token> share-URL segment. Falls
  // back to the demo default when no workspace is loaded.
  activeCompanyDisplayName: string
}

const CompanyContext = createContext<Ctx | null>(null)

export function CompanyProvider({ children }: { children: ReactNode }) {
  const { workspace, activeWorkspace } = useWorkspace()
  // Multi-workspace: the ACTIVE workspace's dataset slug is what every
  // dataset-keyed call feeds on (the default workspace's dataset is the bare
  // company slug, so single-workspace behavior is unchanged). Falls back to
  // the company slug while the workspaces list is still loading.
  const [activeCompany, setActiveCompany] = useActiveCompany(
    activeWorkspace?.dataset ?? workspace?.slug ?? null,
  )
  const activeCompanyDisplayName = workspace?.display_name ?? DEMO_DEFAULT_COMPANY_SLUG
  return (
    <CompanyContext.Provider
      value={{ activeCompany, setActiveCompany, activeCompanyDisplayName }}
    >
      {children}
    </CompanyContext.Provider>
  )
}

export function useCompany(): Ctx {
  const ctx = useContext(CompanyContext)
  if (!ctx) {
    // Default outside provider — keeps screens renderable in storybook / tests.
    return {
      activeCompany: DEMO_DEFAULT_COMPANY_SLUG,
      setActiveCompany: () => {},
      activeCompanyDisplayName: DEMO_DEFAULT_COMPANY_SLUG,
    }
  }
  return ctx
}
