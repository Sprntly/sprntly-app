"use client"

import { createContext, useContext, type ReactNode } from "react"
import { useActiveDataset } from "../lib/useActiveDataset"

type Ctx = {
  activeDataset: string
  setActiveDataset: (slug: string) => void
}

const DatasetContext = createContext<Ctx | null>(null)

export function DatasetProvider({ children }: { children: ReactNode }) {
  const [activeDataset, setActiveDataset] = useActiveDataset()
  return (
    <DatasetContext.Provider value={{ activeDataset, setActiveDataset }}>
      {children}
    </DatasetContext.Provider>
  )
}

export function useDataset(): Ctx {
  const ctx = useContext(DatasetContext)
  if (!ctx) {
    // Default outside provider — keeps screens renderable in storybook / tests.
    return {
      activeDataset: "asurion",
      setActiveDataset: () => {},
    }
  }
  return ctx
}
