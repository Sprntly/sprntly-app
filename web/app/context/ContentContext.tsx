"use client"

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import {
  DEFAULT_HOME_STARTER_CARDS,
  DEFAULT_ONDEMAND_STARTERS,
  type AppContentState,
} from "../types/content"

const EMPTY: AppContentState = {
  userName: null,
  userEmail: null,
  userInitials: null,
  homeHeadline: null,
  homeSub: null,
  homeStarterCards: DEFAULT_HOME_STARTER_CARDS,
  brief: {
    weekRange: null,
    subline: null,
    docSubline: null,
    docKicker: null,
    docHeader: null,
    docFooter: null,
    impactEyebrow: null,
    impactHeadlineLead: null,
    impactHeadlineEmphasis1: null,
    impactHeadlineMid: null,
    impactHeadlineEmphasis2: null,
    impactHeadlineTrail: null,
    impactStats: [],
    metaLines: [],
    sections: [],
  },
  pastWeeks: [],
  shipped: { stats: [], primary: [], supporting: [] },
  conversations: [],
  ondemandStarters: DEFAULT_ONDEMAND_STARTERS,
  detail: null,
  briefDetails: {},
  prd: null,
  evidence: null,
  teamMembers: [],
  teamPending: [],
  connectorCategories: [],
  connectedConnectorIds: [],
  sidebarBriefCount: null,
  sidebarConvCount: null,
  aiScreenChips: {},
}

type ContentContextValue = {
  content: AppContentState
  setContent: (patch: Partial<AppContentState>) => void
  replaceContent: (next: AppContentState) => void
  resetContent: () => void
}

const ContentContext = createContext<ContentContextValue | null>(null)

function mergeContent(
  prev: AppContentState,
  patch: Partial<AppContentState>,
): AppContentState {
  const next: AppContentState = { ...prev }
  for (const key of Object.keys(patch) as (keyof AppContentState)[]) {
    const val = patch[key]
    if (val === undefined) continue
    if (key === "brief") {
      next.brief = { ...prev.brief, ...(val as AppContentState["brief"]) }
    } else if (key === "shipped") {
      next.shipped = { ...prev.shipped, ...(val as AppContentState["shipped"]) }
    } else {
      ;(next as unknown as Record<string, unknown>)[key] = val
    }
  }
  return next
}

export function ContentProvider({ children }: { children: ReactNode }) {
  const [content, setContentState] = useState<AppContentState>(EMPTY)

  const setContent = useCallback((patch: Partial<AppContentState>) => {
    setContentState((prev) => mergeContent(prev, patch))
  }, [])

  const replaceContent = useCallback((next: AppContentState) => {
    setContentState(next)
  }, [])

  const resetContent = useCallback(() => {
    setContentState(EMPTY)
  }, [])

  const value = useMemo(
    () => ({ content, setContent, replaceContent, resetContent }),
    [content, setContent, replaceContent, resetContent],
  )

  return (
    <ContentContext.Provider value={value}>{children}</ContentContext.Provider>
  )
}

export function useContent() {
  const ctx = useContext(ContentContext)
  if (!ctx) {
    throw new Error("useContent must be used within ContentProvider")
  }
  return ctx
}
