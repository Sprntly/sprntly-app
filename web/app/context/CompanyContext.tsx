"use client"

import { createContext, useContext, type ReactNode } from "react"
import { useActiveCompany } from "../lib/useActiveCompany"

type Ctx = {
  activeCompany: string
  setActiveCompany: (slug: string) => void
}

const CompanyContext = createContext<Ctx | null>(null)

export function CompanyProvider({ children }: { children: ReactNode }) {
  const [activeCompany, setActiveCompany] = useActiveCompany()
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
      activeCompany: "asurion",
      setActiveCompany: () => {},
    }
  }
  return ctx
}
