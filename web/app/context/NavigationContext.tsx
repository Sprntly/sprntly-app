"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type ReactNode,
} from "react"
import { usePathname, useRouter } from "next/navigation"
import type { ScreenId } from "../types"
import type { AskResponse } from "../lib/api"
import type { PrdState } from "../types/content"
import { pathForScreen, screenIdFromPathname } from "../lib/routes"

/** Top search hands off `/v1/ask` results to Ask Sprntly (in-page thread) without a second request. */
export type PendingSearchHandoff = { query: string; reply: AskResponse; convId: string }

/** A question started from the weekly-brief surface. The brief is chat-read-only:
 *  a question typed there must NOT thread inline into the brief — it opens its own
 *  chat tab. BriefChat fills this; ChatScreen consumes it once, spawning a fresh
 *  tab seeded with the query (one new tab per chat started from the brief). */
export type PendingChatHandoff = { query: string }

/** The brief-insight pointer a PRD is generated from / anchored to. Null for a
 *  backlog PRD (no insight_index) — it renders from the PRD payload alone. */
export type PrdTabMeta = { briefId: number; insightIndex: number }

/** A request to open a PRD as a NEW CHAT TAB on the chat surface, with the
 *  right-side content panel (Evidence / PRD / Tickets) sliding over it. Every
 *  "view PRD" / "generate PRD" affordance (brief finding cards, the brief
 *  composer, a backlog item) hands one of these off via `openPrdTab`; ChatScreen
 *  consumes it once, spawns a fresh chat tab, drives the source, and opens the
 *  panel. `title` labels the tab. The `source` discriminant says where the PRD
 *  comes from:
 *   - `ready`          — the caller already holds the PrdState (just show it)
 *   - `generate`       — kick off brief-insight PRD generation (runPrdGeneration)
 *   - `generateBacklog`— kick off backlog PRD generation (runPrdGenerationFromBacklog)
 *   - `load`           — fetch an already-generated PRD by id (loadPrdById) */
export type PrdTabRequest = {
  title: string
  /** The insight's body/description text (from the originating brief finding),
   *  shown under the title in the chat insight message so the opening card
   *  carries the finding's content, not just its heading. Optional — only the
   *  brief-card paths (view/generate PRD) carry it; backlog / ready-from-content
   *  paths omit it and the body simply isn't rendered. */
  insightBody?: string
  source:
    | { kind: "ready"; prd: PrdState; meta: PrdTabMeta | null }
    | { kind: "generate"; meta: PrdTabMeta }
    | { kind: "generateBacklog"; backlogItemId: string }
    | { kind: "load"; prdId: number; meta: PrdTabMeta | null }
}

const AI_PANEL_W_KEY = "sprntly-ai-panel-width"
const AI_PANEL_C_KEY = "sprntly-ai-panel-collapsed"
export const AI_PANEL_WIDTH_DEFAULT = 380
export const AI_PANEL_WIDTH_MIN = 280
export const AI_PANEL_WIDTH_MAX = 560
/** Narrow dock when the side assistant is collapsed (mark + bubble on one row). */
export const AI_PANEL_COLLAPSED_WIDTH = 84

/** Below this viewport width the full sidebar (202px) + the 60vw content panel
 *  squeeze the brief/chat column too far, so opening the panel auto-collapses
 *  the sidebar to its 60px rail. Above it there's room to keep both. The lower
 *  900px bound is the mobile breakpoint where the sidebar is hidden anyway. */
export const CPANEL_AUTO_COLLAPSE_MAX_W = 1600

interface NavigationContextType {
  currentScreen: ScreenId
  goTo: (screen: ScreenId) => void
  /** Open a FRESH chat on the unified home surface. The home surface (`/`,
   *  ChatScreen) defaults to the pinned Monday-brief tab on a fresh load, so a
   *  plain `goTo("chat")` would land on the brief — not a new chat. This pushes
   *  `/?new=1`; ChatScreen consumes the one-shot `new` param on mount/param-change
   *  to start a new chat (via startNewThread) and strips it so a later refresh
   *  doesn't re-trigger. Works whether the surface is freshly mounted or already
   *  on screen (the search-param change re-runs the consume effect). */
  goToNewChat: () => void

  // Drawer state
  activeDrawer: "claude" | "ticket" | "design-agent" | null
  openDrawer: (drawer: "claude" | "ticket" | "design-agent") => void
  closeDrawers: () => void

  // Content panel (Evidence / PRD / Tickets — opens in-place instead of navigating)
  contentPanelTab: "evidence" | "prd" | "tickets" | null
  openContentPanel: (tab: "evidence" | "prd" | "tickets") => void
  closeContentPanel: () => void

  // Modal state
  activeModal: "approve" | "invite" | "generate" | null
  openModal: (modal: "approve" | "invite" | "generate") => void
  closeModal: () => void

  // Share menu
  shareMenuOpen: boolean
  setShareMenuOpen: (open: boolean) => void

  // Review past menu
  reviewPastOpen: boolean
  setReviewPastOpen: (open: boolean) => void

  // Toast
  toast: { title: string; sub: string; link?: string; onAction?: () => void; persist?: boolean } | null
  showToast: (title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => void
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

  /** Filled by the weekly-brief composer when a chat is started there; consumed
   *  once by ChatScreen, which opens a fresh chat tab seeded with the query. */
  pendingChatHandoff: PendingChatHandoff | null
  setPendingChatHandoff: (value: PendingChatHandoff | null) => void

  /** Filled by any "view/generate PRD" affordance; consumed once by ChatScreen,
   *  which opens a fresh chat tab and slides the content panel (Evidence / PRD /
   *  Tickets) over it. */
  pendingPrdTab: PrdTabRequest | null
  setPendingPrdTab: (value: PrdTabRequest | null) => void
  /** Open a PRD as a new chat tab (with the right-side content panel over it):
   *  store the request and route to the chat surface (`/`) so ChatScreen mounts
   *  and consumes it. The single entry point for "PRD opens in a new chat". */
  openPrdTab: (request: PrdTabRequest) => void

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

  const [activeDrawer, setActiveDrawer] = useState<"claude" | "ticket" | "design-agent" | null>(null)
  const [contentPanelTab, setContentPanelTab] = useState<"evidence" | "prd" | "tickets" | null>(null)
  const [activeModal, setActiveModal] = useState<"approve" | "invite" | "generate" | null>(null)
  const [shareMenuOpen, setShareMenuOpen] = useState(false)
  const [reviewPastOpen, setReviewPastOpen] = useState(false)
  const [toast, setToast] = useState<{ title: string; sub: string; link?: string; onAction?: () => void; persist?: boolean } | null>(null)
  const [aiBarValue, setAIBarValue] = useState("")
  const [pendingSearchHandoff, setPendingSearchHandoff] = useState<PendingSearchHandoff | null>(null)
  const [pendingOndemandDraft, setPendingOndemandDraft] = useState<string | null>(null)
  const [pendingChatHandoff, setPendingChatHandoff] = useState<PendingChatHandoff | null>(null)
  const [pendingPrdTab, setPendingPrdTab] = useState<PrdTabRequest | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [aiPanelWidth, setAiPanelWidthState] = useState(AI_PANEL_WIDTH_DEFAULT)
  /** Default collapsed; expanded only if user saved `sprntly-ai-panel-collapsed=0`. */
  const [aiPanelCollapsed, setAiPanelCollapsed] = useState(true)
  /** True while the sidebar is collapsed *by the content panel* (not the user),
   *  so it can be restored when the panel closes. Cleared on any manual toggle. */
  const autoCollapsedRef = useRef(false)
  /** The route-change effect below closes the content panel on every pathname
   *  change. `openPrdTab` routes to `/` for the sole purpose of OPENING the PRD
   *  panel there — that one navigation must not trigger the close. This flag,
   *  set by openPrdTab, tells the effect to skip the close for the imminent
   *  arrival at `/`; it's consumed on the next real pathname change. */
  const skipPanelCloseOnNavRef = useRef(false)
  /** Previous pathname, so the route-change effect can tell a genuine navigation
   *  from a no-op re-run and only act on real changes. */
  const prevPathnameRef = useRef(pathname)

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

  // The content panel (Evidence / PRD / Tickets) opens in-place without changing
  // the route, so a route change normally means the user navigated to another
  // page — close the panel so it never lingers over the new screen. The one
  // exception is openPrdTab's own route to `/`: that navigation exists to OPEN
  // the panel, so skip the close for it (else the close races the deferred open
  // in ChatScreen and swallows it — Next updates usePathname inside a transition,
  // so the close can land after the open). The flag is consumed on this change.
  useEffect(() => {
    const prev = prevPathnameRef.current
    if (prev === pathname) return // no real navigation — nothing to close
    prevPathnameRef.current = pathname
    const skip = skipPanelCloseOnNavRef.current && pathname === "/"
    skipPanelCloseOnNavRef.current = false
    if (skip) return
    setContentPanelTab(null)
  }, [pathname])

  const toggleSidebar = useCallback(() => {
    // A manual toggle takes over from any panel-driven auto-collapse, so the
    // panel-close restore below won't fight the user's choice.
    autoCollapsedRef.current = false
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

  // On a normal-size laptop or smaller, the full sidebar and the 60vw content
  // panel can't share the row without crushing the brief/chat column — so when
  // the panel opens at those widths we collapse the sidebar to its rail, then
  // restore it when the panel closes. The collapse is transient (not persisted),
  // so it never overwrites the user's saved sidebar preference.
  useEffect(() => {
    if (typeof window === "undefined") return
    if (contentPanelTab) {
      const w = window.innerWidth
      if (w > 900 && w <= CPANEL_AUTO_COLLAPSE_MAX_W) {
        setSidebarCollapsed((prev) => {
          if (prev) return prev // already collapsed → nothing to restore later
          autoCollapsedRef.current = true
          return true
        })
      }
    } else if (autoCollapsedRef.current) {
      autoCollapsedRef.current = false
      setSidebarCollapsed(false)
    }
  }, [contentPanelTab])

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

  const goToNewChat = useCallback(() => {
    setActiveDrawer(null)
    setActiveModal(null)
    setShareMenuOpen(false)
    setReviewPastOpen(false)
    setPendingOndemandDraft(null)
    // `/?new=1` — the one-shot "start a fresh chat" signal ChatScreen consumes on
    // mount/param-change (then strips). Without the param, `/` defaults to the
    // pinned brief tab, so this is what makes "New chat" land on a new chat.
    router.push("/?new=1")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openPrdTab = useCallback((request: PrdTabRequest) => {
    setPendingPrdTab(request)
    // This navigation to `/` is *for* opening the PRD panel — tell the
    // route-change effect not to close the panel when we land there.
    skipPanelCloseOnNavRef.current = true
    // Route to the chat surface so ChatScreen mounts (from /brief, /backlog, …)
    // and its pending-PRD-tab effect consumes the request — spawning the tab and
    // opening the panel. Harmless when already on `/` (the state change alone
    // drives consumption). ChatScreen defers the panel-open past the route
    // change so the pathname-driven panel-close doesn't swallow it.
    router.push("/")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openDrawer = useCallback((drawer: "claude" | "ticket" | "design-agent") => {
    setActiveDrawer(drawer)
  }, [])

  const closeDrawers = useCallback(() => {
    setActiveDrawer(null)
  }, [])

  const openContentPanel = useCallback((tab: "evidence" | "prd" | "tickets") => {
    setContentPanelTab(tab)
  }, [])

  const closeContentPanel = useCallback(() => {
    setContentPanelTab(null)
  }, [])

  const openModal = useCallback((modal: "approve" | "invite" | "generate") => {
    setActiveModal(modal)
  }, [])

  const closeModal = useCallback(() => {
    setActiveModal(null)
  }, [])

  const showToast = useCallback((title: string, sub: string, link?: string, opts?: { onAction?: () => void; persist?: boolean }) => {
    setToast({ title, sub, link, onAction: opts?.onAction, persist: opts?.persist })
    if (!opts?.persist) {
      setTimeout(() => setToast(null), 5500)
    }
  }, [])

  const hideToast = useCallback(() => {
    setToast(null)
  }, [])

  return (
    <NavigationContext.Provider
      value={{
        currentScreen,
        goTo,
        goToNewChat,
        activeDrawer,
        openDrawer,
        closeDrawers,
        contentPanelTab,
        openContentPanel,
        closeContentPanel,
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
        pendingChatHandoff,
        setPendingChatHandoff,
        pendingPrdTab,
        setPendingPrdTab,
        openPrdTab,
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
