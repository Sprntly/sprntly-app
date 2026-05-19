"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useMemo,
  type ReactNode,
} from "react"
import { usePathname, useRouter } from "next/navigation"
import type { ScreenId } from "../types"
import type { AskResponse } from "../lib/api"
import { pathForScreen, screenIdFromPathname } from "../lib/routes"

/** Top search hands off `/v1/ask` results to Ask Sprntly (in-page thread) without a second request. */
export type PendingSearchHandoff = { query: string; reply: AskResponse; convId: string }

const AI_PANEL_W_KEY = "sprntly-ai-panel-width"
const AI_PANEL_C_KEY = "sprntly-ai-panel-collapsed"
export const AI_PANEL_WIDTH_DEFAULT = 380
export const AI_PANEL_WIDTH_MIN = 280
export const AI_PANEL_WIDTH_MAX = 560
/** Narrow dock when the side assistant is collapsed (mark + bubble on one row). */
export const AI_PANEL_COLLAPSED_WIDTH = 84

interface NavigationContextType {
  currentScreen: ScreenId
  goTo: (screen: ScreenId) => void

  // Drawer state
  activeDrawer: "claude" | "ticket" | null
  openDrawer: (drawer: "claude" | "ticket") => void
  closeDrawers: () => void

  // Modal state
  activeModal: "approve" | "invite" | null
  openModal: (modal: "approve" | "invite") => void
  closeModal: () => void

  // Share menu
  shareMenuOpen: boolean
  setShareMenuOpen: (open: boolean) => void

  // Review past menu
  reviewPastOpen: boolean
  setReviewPastOpen: (open: boolean) => void

  // Toast
  toast: { title: string; sub: string; link?: string } | null
  showToast: (title: string, sub: string, link?: string) => void
  hideToast: () => void

  // AI bar
  aiBarValue: string
  setAIBarValue: (value: string) => void

  /** Filled after a successful ask from elsewhere; consumed once by Home chat. */
  pendingSearchHandoff: PendingSearchHandoff | null
  setPendingSearchHandoff: (value: PendingSearchHandoff | null) => void

  /** Filled from Home starter chips; consumed once by Home composer. */
  pendingOndemandDraft: string | null
  setPendingOndemandDraft: (value: string | null) => void

  /** Narrow icon-only rail vs full labels */
  sidebarCollapsed: boolean
  toggleSidebar: () => void

  /** Right AI panel (Brief / Evidence / PRD): width in px when expanded */
  aiPanelWidth: number
  setAiPanelWidth: (width: number) => void
  aiPanelCollapsed: boolean
  toggleAiPanelCollapsed: () => void
  /** Expand the right assistant rail (no-op on bottom layout / when AI bar hidden). */
  expandAiPanel: () => void
}

const NavigationContext = createContext<NavigationContextType | null>(null)

export function NavigationProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const currentScreen = useMemo(() => screenIdFromPathname(pathname), [pathname])

  const [activeDrawer, setActiveDrawer] = useState<"claude" | "ticket" | null>(null)
  const [activeModal, setActiveModal] = useState<"approve" | "invite" | null>(null)
  const [shareMenuOpen, setShareMenuOpen] = useState(false)
  const [reviewPastOpen, setReviewPastOpen] = useState(false)
  const [toast, setToast] = useState<{ title: string; sub: string; link?: string } | null>(null)
  const [aiBarValue, setAIBarValue] = useState("")
  const [pendingSearchHandoff, setPendingSearchHandoff] = useState<PendingSearchHandoff | null>(null)
  const [pendingOndemandDraft, setPendingOndemandDraft] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [aiPanelWidth, setAiPanelWidthState] = useState(AI_PANEL_WIDTH_DEFAULT)
  /** Default collapsed; expanded only if user saved `sprntly-ai-panel-collapsed=0`. */
  const [aiPanelCollapsed, setAiPanelCollapsed] = useState(true)

  useEffect(() => {
    try {
      if (localStorage.getItem("sprntly-sidebar-collapsed") === "1") {
        setSidebarCollapsed(true)
      }
      const w = localStorage.getItem(AI_PANEL_W_KEY)
      if (w) {
        const n = parseInt(w, 10)
        if (!Number.isNaN(n)) {
          setAiPanelWidthState(
            Math.min(AI_PANEL_WIDTH_MAX, Math.max(AI_PANEL_WIDTH_MIN, n)),
          )
        }
      }
      const aiCollapsed = localStorage.getItem(AI_PANEL_C_KEY)
      if (aiCollapsed === "0") {
        setAiPanelCollapsed(false)
      } else if (aiCollapsed === "1") {
        setAiPanelCollapsed(true)
      }
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    if (typeof document === "undefined") return
    if (sidebarCollapsed) {
      document.documentElement.setAttribute("data-sidebar-collapsed", "")
    } else {
      document.documentElement.removeAttribute("data-sidebar-collapsed")
    }
  }, [sidebarCollapsed])

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem("sprntly-sidebar-collapsed", next ? "1" : "0")
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])

  const setAiPanelWidth = useCallback((width: number) => {
    const clamped = Math.min(
      AI_PANEL_WIDTH_MAX,
      Math.max(AI_PANEL_WIDTH_MIN, Math.round(width)),
    )
    setAiPanelWidthState(clamped)
    try {
      localStorage.setItem(AI_PANEL_W_KEY, String(clamped))
    } catch {
      /* ignore */
    }
  }, [])

  const toggleAiPanelCollapsed = useCallback(() => {
    setAiPanelCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem(AI_PANEL_C_KEY, next ? "1" : "0")
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])

  const expandAiPanel = useCallback(() => {
    setAiPanelCollapsed((prev) => {
      if (!prev) return prev
      try {
        localStorage.setItem(AI_PANEL_C_KEY, "0")
      } catch {
        /* ignore */
      }
      return false
    })
  }, [])

  const goTo = useCallback(
    (screen: ScreenId) => {
      const path = pathForScreen(screen)
      setActiveDrawer(null)
      setActiveModal(null)
      setShareMenuOpen(false)
      setReviewPastOpen(false)
      if (screen !== "chat" && screen !== "ondemand") {
        setPendingOndemandDraft(null)
      }
      router.push(path)
      window.scrollTo({ top: 0, behavior: "instant" })
    },
    [router],
  )

  const openDrawer = useCallback((drawer: "claude" | "ticket") => {
    setActiveDrawer(drawer)
  }, [])

  const closeDrawers = useCallback(() => {
    setActiveDrawer(null)
  }, [])

  const openModal = useCallback((modal: "approve" | "invite") => {
    setActiveModal(modal)
  }, [])

  const closeModal = useCallback(() => {
    setActiveModal(null)
  }, [])

  const showToast = useCallback((title: string, sub: string, link?: string) => {
    setToast({ title, sub, link })
    setTimeout(() => setToast(null), 5500)
  }, [])

  const hideToast = useCallback(() => {
    setToast(null)
  }, [])

  return (
    <NavigationContext.Provider
      value={{
        currentScreen,
        goTo,
        activeDrawer,
        openDrawer,
        closeDrawers,
        activeModal,
        openModal,
        closeModal,
        shareMenuOpen,
        setShareMenuOpen,
        reviewPastOpen,
        setReviewPastOpen,
        toast,
        showToast,
        hideToast,
        aiBarValue,
        setAIBarValue,
        pendingSearchHandoff,
        setPendingSearchHandoff,
        pendingOndemandDraft,
        setPendingOndemandDraft,
        sidebarCollapsed,
        toggleSidebar,
        aiPanelWidth,
        setAiPanelWidth,
        aiPanelCollapsed,
        toggleAiPanelCollapsed,
        expandAiPanel,
      }}
    >
      {children}
    </NavigationContext.Provider>
  )
}

export function useNavigation() {
  const context = useContext(NavigationContext)
  if (!context) {
    throw new Error("useNavigation must be used within a NavigationProvider")
  }
  return context
}
