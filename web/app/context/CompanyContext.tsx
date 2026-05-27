"use client"

import { createContext, useContext, type ReactNode } from "react"
import { useActiveCompany, DEMO_DEFAULT_COMPANY_SLUG } from "../lib/useActiveCompany"
import { useWorkspace } from "./WorkspaceContext"

type Ctx = {
  activeCompany: string
  setActiveCompany: (slug: string) => void
}

const CompanyContext = createContext<Ctx | null>(null)

export function CompanyProvider({ children }: { children: ReactNode }) {
  const { workspace } = useWorkspace()
  const [activeCompany, setActiveCompany] = useActiveCompany(workspace?.slug ?? null)
  return (
    <CompanyContext.Provider value={{ activeCompany, setActiveCompany }}>
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
    }
  }
  return ctx
}
