"use client"

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type MutableRefObject,
  type ReactNode,
} from "react"
import { usePathname, useRouter } from "next/navigation"
import type { ScreenId } from "../types"
import type { AskResponse } from "../lib/api"
import type { DetailState, PrdState } from "../types/content"
import { pathForScreen, screenIdFromPathname } from "../lib/routes"

/** Top search hands off `/v1/ask` results to Ask Sprntly (in-page thread) without a second request. */
export type PendingSearchHandoff = { query: string; reply: AskResponse; convId: string }

/** A question started from the top-insights surface. The brief is chat-read-only:
 *  a question typed there must NOT thread inline into the brief — it opens its own
 *  chat tab. BriefChat fills this; ChatScreen consumes it once, spawning a fresh
 *  tab seeded with the query (one new tab per chat started from the brief). */
export type PendingChatHandoff = { query: string }

/** A passage highlighted in a team document, handed to the chat composer.
 *
 *  `documentId` rides along with the text because the two answer different
 *  halves of the requirement: the EXCERPT is what the user wants to talk
 *  about (and, being in the message, is what grounds the answer), and the ID
 *  is what an edit has to be applied to. */
export type DocumentQuote = { documentId: number; excerpt: string }

/** The brief-insight pointer a PRD is generated from / anchored to. Null for
 *  an ideation PRD (no insight_index) — it renders from the PRD payload alone. */
export type PrdTabMeta = { briefId: number; insightIndex: number }

/** A request to open a document as a NEW CHAT TAB on the chat surface, with the
 *  right-side content panel (Evidence / PRD / Tickets) sliding over it. Every
 *  "view evidence" / "view PRD" / "generate PRD" affordance (brief finding cards,
 *  the brief composer, an ideation item) hands one of these off via `openPrdTab`;
 *  ChatScreen consumes it once, spawns a fresh chat tab, drives the source, and
 *  opens the panel. `title` labels the tab. The `source` discriminant says which
 *  document the tab is about and where it comes from:
 *   - `ready`          — the caller already holds the PrdState (just show it)
 *   - `generate`       — kick off brief-insight PRD generation (runPrdGeneration)
 *   - `generateIdeation`— kick off ideation PRD generation (runPrdGenerationFromIdeation)
 *   - `load`           — fetch an already-generated PRD by id (loadPrdById)
 *   - `resume`         — poll a PRD whose generation was already kicked off
 *                        elsewhere (Artifacts upload, chat PRD-import command)
 *   - `evidence`       — the insight's EVIDENCE is the document: land the panel on
 *                        its Evidence tab and start no PRD work at all. The PRD tab
 *                        is one click away and resolves on demand (ContentPanel). */
export type PrdTabRequest = {
  title: string
  /** The insight's body/description text (from the originating brief finding),
   *  shown under the title in the chat insight message so the opening card
   *  carries the finding's content, not just its heading. Optional — only the
   *  brief-card paths (view/generate PRD) carry it; ideation / ready-from-content
   *  paths omit it and the body simply isn't rendered. */
  insightBody?: string
  /** The user's typed command that opened this PRD tab (e.g. "convert this PRD
   *  into tickets"). When set, ChatScreen seeds the new tab's thread with it +
   *  an acknowledgment turn, so the chat shows WHY the generation started
   *  instead of opening empty next to a spinning panel. */
  seedQuery?: string
  source:
    | { kind: "ready"; prd: PrdState; meta: PrdTabMeta | null }
    | { kind: "generate"; meta: PrdTabMeta }
    | { kind: "generateIdeation"; ideationItemId: string }
    | { kind: "load"; prdId: number; meta: PrdTabMeta | null }
    // Generation was ALREADY kicked off elsewhere (e.g. the PRD-import endpoint
    // returns a 'generating' prd_id): open the tab immediately and re-enter
    // polling in-panel until ready, rather than blocking the caller first.
    // `openTickets` (chat "convert this PRD into tickets" over an attached
    // document) lands the panel on the Tickets tab once the PRD is ready.
    // `origin` picks the seeded acknowledgment wording: 'task' (a chat
    // "generate a PRD for <specific need>" command) vs the default doc-import.
    | { kind: "resume"; prdId: number; meta: PrdTabMeta | null; openTickets?: boolean; origin?: "import" | "task" }
    // Evidence-first open (a Top Insights card's "View Evidence"). `detail` is the
    // finding's drill-down state, which scopes ContentPanel's Evidence tab to this
    // insight — that tab owns loading/generating the evidence itself.
    | { kind: "evidence"; meta: PrdTabMeta; detail: DetailState | null }
}

/** The right-side content panel's tabs. Evidence → PRD → Tickets are the PRD
 *  pipeline; Reports is the thread's captured report documents, which hang off
 *  the conversation rather than off a PRD. */
export type ContentPanelTab =
  | "evidence" | "prd" | "tickets" | "reports"
  /** A team document (custom artifact) — the "Others" library's kind. Like
   *  Reports it hangs off the CHAT THREAD rather than off the PRD, so it sits
   *  after the pipeline and stays hidden until the thread actually has one. */
  | "document"

/** A request to open a captured report in the thread it was generated in.
 *
 *  A report's home is its chat: clicking one in Artifacts should land you in that
 *  conversation with the panel's Reports tab open on the document, not float a
 *  standalone drawer over the library. The caller writes the ordinary
 *  `sprntly_resume_conv` hand-off (the same one ChatsScreen and the command
 *  palette use to reopen a chat) and fills this; ChatScreen consumes it once the
 *  resumed tab is actually active, then opens the panel on that report. */
export type ReportFocusRequest = { conversationId: number; reportId: number }

/** A request to open a STANDALONE ticket set in the chat it was generated in.
 *
 *  Exactly the report hand-off's shape and posture, one artifact over: a set's
 *  home is its thread, so clicking one in Artifacts lands you in that
 *  conversation with the panel's Tickets tab on the set — not a bare drawer
 *  floating over the library. The caller writes the ordinary
 *  `sprntly_resume_conv` hand-off and fills this; ChatScreen consumes it once
 *  the resumed tab is actually active, then loads the set and opens the panel.
 *  No new panel tab: a set reads on the existing `"tickets"` one. */
export type TicketSetFocusRequest = { conversationId: number; ticketSetId: number }

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
  /** Open the WORKBENCH: the home surface restored to the tab the user was last
   *  on, EXCLUDING the pinned Top Insights tab. The sidebar has two doors into
   *  the same tabbed surface — "Top Insights" always lands on the pinned first
   *  tab, "Workbench" always lands on your open work. Pushes `/?tab=last`;
   *  ChatScreen consumes the one-shot param (then strips it), activating the
   *  remembered chat tab, or falling back to a fresh chat when none is open.
   *  Same one-shot-param shape as `goToNewChat` so it works whether the surface
   *  is freshly mounted or already on screen. */
  goToWorkbench: () => void

  // Drawer state
  activeDrawer: "claude" | "ticket" | "design-agent" | null
  openDrawer: (drawer: "claude" | "ticket" | "design-agent") => void
  closeDrawers: () => void

  // Content panel (Evidence / PRD / Tickets / Reports — opens in-place instead
  // of navigating). Every artifact of the active thread reads in here.
  contentPanelTab: ContentPanelTab | null
  openContentPanel: (tab: ContentPanelTab) => void
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

  /** A passage the reader highlighted in a team document, on its way to the
   *  chat composer of the thread the document is open beside. Filled by the
   *  Document panel; consumed once by ChatScreen, which quotes it into the
   *  draft so the next question or edit request is ABOUT that passage.
   *
   *  A one-shot handoff rather than shared state, for the same reason the
   *  drafts above are: the excerpt is a message the user is composing, not a
   *  property of the document, and leaving it set would re-quote it on the
   *  next render. */
  pendingDocumentQuote: DocumentQuote | null
  setPendingDocumentQuote: (value: DocumentQuote | null) => void

  /** Filled by the top-insights composer when a chat is started there; consumed
   *  once by ChatScreen, which opens a fresh chat tab seeded with the query. */
  pendingChatHandoff: PendingChatHandoff | null
  setPendingChatHandoff: (value: PendingChatHandoff | null) => void

  /** Filled by any "view evidence" / "view-or-generate PRD" affordance; consumed
   *  once by ChatScreen, which opens a fresh chat tab and slides the content panel
   *  (Evidence / PRD / Tickets) over it. */
  pendingPrdTab: PrdTabRequest | null
  setPendingPrdTab: (value: PrdTabRequest | null) => void
  /** Open a PRD or a finding's evidence as a new chat tab (with the right-side
   *  content panel over it): store the request and route to the chat surface (`/`)
   *  so ChatScreen mounts and consumes it. The single entry point for "an artifact
   *  opens in a new chat". */
  openPrdTab: (request: PrdTabRequest) => void

  /** Filled by `openReportTab`; consumed once by ChatScreen, which opens the
   *  panel's Reports tab on that document as soon as the report's own thread is
   *  the active tab. */
  pendingReportFocus: ReportFocusRequest | null
  setPendingReportFocus: (value: ReportFocusRequest | null) => void
  /** Open a captured report in the chat thread it belongs to. The CALLER must
   *  already have written the `sprntly_resume_conv` hand-off for that
   *  conversation (ChatScreen's resume path spawns/refocuses the tab); this
   *  routes to the chat surface and says which report to land on. */
  openReportTab: (request: ReportFocusRequest) => void

  /** Filled by `openTicketSetTab`; consumed once by ChatScreen, which loads the
   *  set and opens the panel's Tickets tab on it as soon as the set's own thread
   *  is the active tab. */
  pendingTicketSetFocus: TicketSetFocusRequest | null
  setPendingTicketSetFocus: (value: TicketSetFocusRequest | null) => void
  /** Open a standalone ticket set in the chat thread it belongs to. The CALLER
   *  must already have written the `sprntly_resume_conv` hand-off for that
   *  conversation (ChatScreen's resume path spawns/refocuses the tab); this
   *  routes to the chat surface and says which set to land on. */
  openTicketSetTab: (request: TicketSetFocusRequest) => void

  /** Global search / command palette (⌘K). Rendered once by AppShell; the
   *  sidebar trigger and the global hotkey both drive this shared state. */
  paletteOpen: boolean
  openPalette: () => void
  closePalette: () => void
  togglePalette: () => void

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

  /** One-shot guard, mirroring `skipPanelCloseOnNavRef` exactly: set `true`
   *  by `ChatScreen`'s fork-to-private-chat nav (`goToProjectPrivateChat`)
   *  immediately before its synchronous `router.push`, and consumed
   *  (read + reset) by the globally-mounted `useArtifactUrlSync`'s `?prd=`
   *  reflect effect — which would otherwise `router.replace` the just-
   *  generated PRD back onto the URL a tick after the push, reverting the
   *  fork nav. Scoped to that ONE reflect arm only; every other consumer of
   *  this context is unaffected by its presence. */
  skipArtifactReflectOnNavRef: MutableRefObject<boolean>
}

const NavigationContext = createContext<NavigationContextType | null>(null)

export function NavigationProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()
  const currentScreen = useMemo(() => screenIdFromPathname(pathname), [pathname])

  const [activeDrawer, setActiveDrawer] = useState<"claude" | "ticket" | "design-agent" | null>(null)
  const [contentPanelTab, setContentPanelTab] = useState<ContentPanelTab | null>(null)
  const [activeModal, setActiveModal] = useState<"approve" | "invite" | "generate" | null>(null)
  const [shareMenuOpen, setShareMenuOpen] = useState(false)
  const [reviewPastOpen, setReviewPastOpen] = useState(false)
  const [toast, setToast] = useState<{ title: string; sub: string; link?: string; onAction?: () => void; persist?: boolean } | null>(null)
  const [aiBarValue, setAIBarValue] = useState("")
  const [pendingSearchHandoff, setPendingSearchHandoff] = useState<PendingSearchHandoff | null>(null)
  const [pendingOndemandDraft, setPendingOndemandDraft] = useState<string | null>(null)
  const [pendingDocumentQuote, setPendingDocumentQuote] = useState<DocumentQuote | null>(null)
  const [pendingChatHandoff, setPendingChatHandoff] = useState<PendingChatHandoff | null>(null)
  const [pendingPrdTab, setPendingPrdTab] = useState<PrdTabRequest | null>(null)
  const [pendingReportFocus, setPendingReportFocus] = useState<ReportFocusRequest | null>(null)
  const [pendingTicketSetFocus, setPendingTicketSetFocus] = useState<TicketSetFocusRequest | null>(null)
  const [paletteOpen, setPaletteOpen] = useState(false)
  // Default to the collapsed icon rail; a saved "0" preference (see the init
  // effect) expands it on load. Users toggle via the sidebar chevron.
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
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
  /** One-shot fork-nav guard (mirrors `skipPanelCloseOnNavRef` immediately
   *  above) — see the `NavigationContextType` field doc comment. Purely
   *  additive: only `ChatScreen`'s fork-nav sets it, only
   *  `useArtifactUrlSync`'s `?prd=` reflect effect reads/resets it. */
  const skipArtifactReflectOnNavRef = useRef(false)

  useEffect(() => {
    try {
      const savedCollapsed = localStorage.getItem("sprntly-sidebar-collapsed")
      if (savedCollapsed === "0") {
        setSidebarCollapsed(false)
      } else if (savedCollapsed === "1") {
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

  const goToWorkbench = useCallback(() => {
    setActiveDrawer(null)
    setActiveModal(null)
    setShareMenuOpen(false)
    setReviewPastOpen(false)
    setPendingOndemandDraft(null)
    // `/?tab=last` — the one-shot "restore my last non-brief tab" signal
    // ChatScreen consumes (then strips). Without the param, `/` defaults to the
    // pinned brief tab, which is precisely the tab this nav must NOT land on.
    router.push("/?tab=last")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openPrdTab = useCallback((request: PrdTabRequest) => {
    setPendingPrdTab(request)
    // This navigation to `/` is *for* opening the PRD panel — tell the
    // route-change effect not to close the panel when we land there.
    skipPanelCloseOnNavRef.current = true
    // Route to the chat surface so ChatScreen mounts (from /brief, /ideation, …)
    // and its pending-PRD-tab effect consumes the request — spawning the tab and
    // opening the panel. Harmless when already on `/` (the state change alone
    // drives consumption). ChatScreen defers the panel-open past the route
    // change so the pathname-driven panel-close doesn't swallow it.
    router.push("/")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openReportTab = useCallback((request: ReportFocusRequest) => {
    setPendingReportFocus(request)
    // Same posture as openPrdTab: this navigation to `/` exists to OPEN the
    // panel, so the route-change effect above must not close it on arrival.
    skipPanelCloseOnNavRef.current = true
    router.push("/")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openTicketSetTab = useCallback((request: TicketSetFocusRequest) => {
    setPendingTicketSetFocus(request)
    // Same posture as openReportTab: this navigation to `/` exists to OPEN the
    // panel, so the route-change effect above must not close it on arrival.
    skipPanelCloseOnNavRef.current = true
    router.push("/")
    window.scrollTo({ top: 0, behavior: "instant" })
  }, [router])

  const openPalette = useCallback(() => setPaletteOpen(true), [])
  const closePalette = useCallback(() => setPaletteOpen(false), [])
  const togglePalette = useCallback(() => setPaletteOpen((v) => !v), [])

  const openDrawer = useCallback((drawer: "claude" | "ticket" | "design-agent") => {
    setActiveDrawer(drawer)
  }, [])

  const closeDrawers = useCallback(() => {
    setActiveDrawer(null)
  }, [])

  const openContentPanel = useCallback((tab: ContentPanelTab) => {
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
        goToWorkbench,
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
        pendingDocumentQuote,
        setPendingDocumentQuote,
        setPendingOndemandDraft,
        pendingChatHandoff,
        setPendingChatHandoff,
        pendingPrdTab,
        setPendingPrdTab,
        openPrdTab,
        pendingReportFocus,
        setPendingReportFocus,
        openReportTab,
        pendingTicketSetFocus,
        setPendingTicketSetFocus,
        openTicketSetTab,
        paletteOpen,
        openPalette,
        closePalette,
        togglePalette,
        sidebarCollapsed,
        toggleSidebar,
        aiPanelWidth,
        setAiPanelWidth,
        aiPanelCollapsed,
        toggleAiPanelCollapsed,
        expandAiPanel,
        skipArtifactReflectOnNavRef,
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
