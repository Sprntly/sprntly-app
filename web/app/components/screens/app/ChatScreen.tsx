"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
import type { ChatHomeCard } from "../../../types/content"
import { buildHomeChips, type HomeChipItem } from "../../../lib/homeChips"
import { AppLayout } from "./AppLayout"
import { BriefChat, isPrdCommand, prototypeCtaLabel } from "../../shared/BriefChat"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import { AskReplyBody } from "../../shared/AskReplyBody"
import { PrdInputQuestions } from "../../shared/PrdInputQuestions"
import { ChatSuggestionIcon, IconSendUp, IconSparkle } from "../../shared/app-icons"
import { ApiError, askApi, briefApi, type AskResponse, type SkillInfo } from "../../../lib/api"
import { createChatPersistence, replyToText } from "../../../lib/chatPersistence"
import { addToSet, isComposerBusy, removeFromSet, runTabAsk } from "../../../lib/chatAskState"
import { runPrdGeneration, resumePrdGeneration, runPrdGenerationFromBacklog, loadPrdById } from "../../../lib/runPrdGeneration"
import type { PrdTabRequest } from "../../../context/NavigationContext"
import { runEvidenceGeneration, resumeEvidenceGeneration } from "../../../lib/runEvidenceGeneration"
import { runAskGeneration, resumeAskGeneration, getPendingAsk, AskCancelledError } from "../../../lib/runAskGeneration"
import { getPendingJob, insightScope } from "../../../lib/jobResume"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import type { PrdState, PrdContent } from "../../../types/content"
import { useBriefPrototypeMap } from "../../design-agent/useBriefPrototypeMap"
import { prototypePath } from "../../../lib/routes"
import { useRouter, useSearchParams } from "next/navigation"
import { prototypeStateForInsight } from "../../design-agent/briefPrototypeMap.helpers"
import { GenerateModal } from "../../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../../design-agent/GenerationLoadingScreen"
import type { DesignAgentGenResult } from "../../../lib/runDesignAgentGeneration"
import { AGENT_NAME } from "../../../lib/agent"

type ThreadTurn = {
  id: string
  query: string
  reply?: AskResponse
  error?: string
}

type BriefMeta = { briefId: number; insightIndex: number }

type ChatTab = {
  id: string
  title: string
  thread: ThreadTurn[]
  dbConvId: number | null
  /** Brief finding context — enables PRD/evidence generation for this tab. */
  briefMeta: BriefMeta | null
  /** The originating insight's body/description text, shown under the title in
   *  the opening insight message. Null for tabs not opened from a brief finding
   *  (backlog / plain chat) or when the finding had no body. */
  insightBody: string | null
  /** Per-tab cached PRD (not persisted to localStorage — re-generate on reload). */
  prd: PrdState | null
  /** Per-tab cached evidence. */
  evidence: PrdContent | null
  prdGenerating: boolean
  evidenceGenerating: boolean
}

// The Weekly Brief is a pinned, non-closable FIRST tab on this surface.
// It is synthesized in the render — never stored in the `tabs` state or
// localStorage — and is identified by this sentinel id. `activeTabId ===
// BRIEF_TAB_ID` means the brief tab is active (so we render <BriefChat/> instead
// of the chat landing/thread). It is also the default active tab on first load.
const BRIEF_TAB_ID = "brief"

// Placeholder title for a freshly-opened "+" tab before the user sends their
// first message. The tab is visible+active in the strip immediately (so the user
// can see they're on a new tab and switch back), and gets its real title from the
// first message on send (see submitAsk's first-send rename).
export const NEW_CHAT_TITLE = "New chat"

const DEFAULT_HOME_CHIPS: HomeChipItem[] = [
  { kind: "home", card: { id: "def-brief", icon: "sparkle", title: "View weekly brief", desc: "", target: "brief" } },
  { kind: "starter", card: { id: "def-analyze", icon: "chart", title: "Analyze data", desc: "", target: "ondemand", prompt: "Analyze our key product metrics and identify the top opportunities." } },
  { kind: "starter", card: { id: "def-draft", icon: "document", title: "Draft quarterly report", desc: "", target: "ondemand", prompt: "Draft a quarterly product report with key metrics, wins, and next steps." } },
  { kind: "starter", card: { id: "def-proto", icon: "rocket", title: "Prototype", desc: "", target: "ondemand", prompt: "Help me prototype the top feature in our product roadmap." } },
]

export function ChatScreen() {
  const {
    currentScreen,
    goTo,
    setAIBarValue,
    expandAiPanel,
    pendingSearchHandoff,
    setPendingSearchHandoff,
    pendingOndemandDraft,
    setPendingOndemandDraft,
    pendingChatHandoff,
    setPendingChatHandoff,
    pendingPrdTab,
    setPendingPrdTab,
    openPrdTab,
    showToast,
    openContentPanel,
    closeContentPanel,
    contentPanelTab,
  } = useNavigation()
  const router = useRouter()
  const searchParams = useSearchParams()
  const auth = useAuth()
  const { profile, workspace } = useWorkspace()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState<number | null>(null)
  // Company-scoped localStorage keys so different tenants never share chat state.
  const tabsKey = `sprntly_chat_tabs_${activeCompany}`
  const activeTabKey = `sprntly_chat_active_tab_${activeCompany}`

  const [tabs, setTabs] = useState<ChatTab[]>(() => {
    try {
      const saved = localStorage.getItem(tabsKey)
      if (!saved) return []
      // Restore with defaults for fields not persisted (prd/evidence are large — re-generate on reload)
      return (JSON.parse(saved) as Partial<ChatTab>[]).map((t) => ({
        id: t.id ?? "",
        title: t.title ?? "",
        thread: t.thread ?? [],
        dbConvId: t.dbConvId ?? null,
        briefMeta: t.briefMeta ?? null,
        insightBody: t.insightBody ?? null,
        prd: null,
        evidence: null,
        prdGenerating: false,
        evidenceGenerating: false,
      }))
    } catch { return [] }
  })
  // Ref kept in sync so callbacks can read current tabs without adding to deps
  const tabsRef = useRef<ChatTab[]>(tabs)
  tabsRef.current = tabs
  // Track which turn IDs have already been animated so re-mounting a tab doesn't
  // restart the typing animation from scratch.
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const [activeTabId, setActiveTabId] = useState<string | null>(() => {
    try {
      const stored = localStorage.getItem(activeTabKey)
      // First load (no persisted active tab) → default to the pinned brief tab.
      // A persisted "" means the user was on the chat landing/new-chat (active
      // tab = null), so we honour that and DON'T fall back to the brief tab.
      if (stored == null) return BRIEF_TAB_ID
      return stored || null
    } catch { return BRIEF_TAB_ID }
  })
  // Mirror of activeTabId for async closures — a background PRD generation/load
  // only pushes its result into the shared content when its own tab is still
  // active, so it never stomps a tab the user has since switched to.
  const activeTabIdRef = useRef<string | null>(activeTabId)
  activeTabIdRef.current = activeTabId
  // True while this ChatScreen is mounted. Detached Ask polls read it to stop
  // (and LEAVE their persisted ask_id in place) when the user navigates to
  // another screen — so a background completion isn't dropped by a no-op state
  // write; the mount-time resume effect re-attaches and populates on return.
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])
  // When set, ChatScreen slides the content panel (Evidence / PRD / Tickets) open
  // on the NEXT commit — deferred one commit so the route-change panel-close (a
  // PRD opened from another surface routes to `/`) can't swallow it.
  const [prdPanelPending, setPrdPanelPending] = useState(false)

  // When the active company changes (user switches workspace or logs in as
  // a different user), reload tabs from the new company-scoped storage so we
  // never show another tenant's chat threads.
  const prevCompanyRef = useRef(activeCompany)
  useEffect(() => {
    if (prevCompanyRef.current === activeCompany) return
    prevCompanyRef.current = activeCompany
    try {
      const saved = localStorage.getItem(tabsKey)
      if (saved) {
        setTabs((JSON.parse(saved) as Partial<ChatTab>[]).map((t) => ({
          id: t.id ?? "", title: t.title ?? "", thread: t.thread ?? [],
          dbConvId: t.dbConvId ?? null, briefMeta: t.briefMeta ?? null,
          insightBody: t.insightBody ?? null,
          prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
        })))
      } else {
        setTabs([])
      }
      const storedActive = localStorage.getItem(activeTabKey)
      // No persisted active tab for this company → default to the pinned brief
      // tab; a persisted "" honours the chat landing (active tab = null).
      setActiveTabId(storedActive == null ? BRIEF_TAB_ID : storedActive || null)
    } catch {
      setTabs([])
      setActiveTabId(BRIEF_TAB_ID)
    }
  }, [activeCompany, tabsKey, activeTabKey])

  // Persist tabs to localStorage — strip large/transient fields (prd, evidence, *Generating)
  useEffect(() => {
    try {
      const slim = tabs.map(({ prd: _p, evidence: _e, prdGenerating: _pg, evidenceGenerating: _eg, ...rest }) => rest)
      localStorage.setItem(tabsKey, JSON.stringify(slim))
    } catch { /* ignore */ }
  }, [tabs, tabsKey])
  useEffect(() => {
    try { localStorage.setItem(activeTabKey, activeTabId ?? "") } catch { /* ignore */ }
  }, [activeTabId, activeTabKey])

  // The pinned brief tab is synthesized (not in `tabs`), so when it's active
  // `activeTab` is null. `isBriefTab` lets the render swap in <BriefChat/> for
  // the chat landing/thread + composer.
  const isBriefTab = activeTabId === BRIEF_TAB_ID
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null
  const thread = activeTab?.thread ?? []

  // ── Prototype map for the active tab's brief (one fetch per briefId) ───────
  const chatBriefId = activeTab?.briefMeta?.briefId ?? null
  const { entriesByInsight: chatEntriesByInsight, loading: chatMapLoading } = useBriefPrototypeMap(chatBriefId)

  const chatInsightState = useMemo(() => {
    if (!activeTab?.briefMeta) return null
    return prototypeStateForInsight(chatEntriesByInsight, activeTab.briefMeta.insightIndex)
  }, [activeTab?.briefMeta, chatEntriesByInsight])

  // GenerateModal / LoadingScreen state for the chat surface
  const chatGenLoadingRef = useRef(false)
  const [chatGenLoading, setChatGenLoading] = useState(false)
  const [chatGenPrdId, setChatGenPrdId] = useState<number | null>(null)
  const [chatGenFigmaKey, setChatGenFigmaKey] = useState<string | null>(null)
  const [chatGenGithubRepo, setChatGenGithubRepo] = useState<string | null>(null)
  const [chatGenProtoId, setChatGenProtoId] = useState<number | null>(null)
  const [chatGenModalOpen, setChatGenModalOpen] = useState(false)

  const handleChatGenStart = useCallback((ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => {
    setChatGenFigmaKey(ctx?.figmaFileKey ?? null)
    setChatGenGithubRepo(ctx?.githubRepo ?? null)
    setChatGenProtoId(null)
    chatGenLoadingRef.current = true
    setChatGenLoading(true)
  }, [])

  const handleChatGenDone = useCallback((result?: DesignAgentGenResult) => {
    chatGenLoadingRef.current = false
    setChatGenLoading(false)
    setChatGenModalOpen(false)
    if (result?.ok && chatGenPrdId != null) {
      router.push(prototypePath(chatGenPrdId))
    }
  }, [chatGenPrdId, router])

  const handleChatPrototype = useCallback(() => {
    if (chatInsightState?.hasPrd && chatInsightState.prototypeReady && chatInsightState.prdId != null) {
      router.push(prototypePath(chatInsightState.prdId))
    } else if (chatInsightState?.hasPrd && !chatInsightState.prototypeReady && chatInsightState.prdId != null) {
      setChatGenPrdId(chatInsightState.prdId)
      setChatGenModalOpen(true)
    } else {
      goTo("prototype")
    }
  }, [chatInsightState, router, goTo])

  const setThread = useCallback((updater: ThreadTurn[] | ((prev: ThreadTurn[]) => ThreadTurn[])) => {
    setTabs((prev) => prev.map((t) => {
      if (t.id !== activeTabId) return t
      const next = typeof updater === "function" ? updater(t.thread) : updater
      return { ...t, thread: next }
    }))
  }, [activeTabId])

  // A "User input needed" answer patched the PRD (scoped edit). Refresh the
  // active tab's cached PRD + the shared content panel so the change shows live.
  const handleInputPrdUpdated = useCallback((prd: PrdState) => {
    setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prd } : t))
    setContent({ prd })
  }, [activeTabId, setContent])
  const [draft, setDraft] = useState("")
  // Per-tab busy tracking — a tab is "busy" while its own ask is in flight. The
  // composer's busy/disabled state is derived from the ACTIVE tab only (see the
  // `busy` const below `activeTab`), so switching to an idle tab shows an enabled
  // composer even while another tab is still loading.
  const [busyTabs, setBusyTabs] = useState<ReadonlySet<string>>(new Set())
  // Composer busy/disabled + "thinking" indicator reflect ONLY the active tab's
  // in-flight status. Another tab being mid-ask must not disable this composer.
  const busy = isComposerBusy(busyTabs, activeTabId)
  const [showSlash, setShowSlash] = useState(false)
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [slashFilter, setSlashFilter] = useState("")
  const [attachments, setAttachments] = useState<{ name: string; content: string }[]>([])
  // Per-tab in-flight guard — keyed by tabId. Prevents a tab from firing a second
  // ask while its own is still in flight, while letting OTHER tabs send concurrently.
  const askingTabsRef = useRef<Set<string>>(new Set())
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Attach: read file as text and add to context
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    Array.from(files).forEach((file) => {
      const reader = new FileReader()
      reader.onload = () => {
        const content = reader.result as string
        setAttachments((prev) => [...prev, { name: file.name, content: content.slice(0, 50000) }])
        showToast("Attached", `"${file.name}" added as context.`)
      }
      reader.readAsText(file)
    })
    e.target.value = "" // reset so same file can be re-selected
  }, [showToast])

  // Load skills on mount
  useEffect(() => {
    askApi.skills().then((r) => setSkills(r.skills)).catch(() => {
      // Hardcoded fallback if endpoint not available
      setSkills([
        { id: "prd-author", label: "Generate PRD", trigger: "/prd", description: "Draft a product requirements document" },
        { id: "prioritize", label: "Prioritize", trigger: "/prioritize", description: "Rank ideas using RICE, ICE, MoSCoW, or WSJF" },
        { id: "user-stories", label: "User stories", trigger: "/stories", description: "Break a PRD into user stories" },
        { id: "backlog-triage", label: "Triage backlog", trigger: "/triage", description: "Clean up backlog: cluster, dedupe" },
        { id: "decision-memo", label: "Decision memo", trigger: "/decide", description: "Structure a build/buy decision" },
        { id: "feedback-synthesis", label: "Feedback synthesis", trigger: "/feedback", description: "Synthesize feedback into themes" },
        { id: "competitive-intelligence-review", label: "Competitive analysis", trigger: "/compete", description: "Competitive intelligence review" },
        { id: "incident-runbook", label: "Incident runbook", trigger: "/incident", description: "Generate incident response runbook" },
        { id: "fact-check", label: "Fact-check", trigger: "/factcheck", description: "Verify claims against sources" },
      ])
    })
  }, [])

  // Create a new tab or, if a tab with the same title already exists, switch to it
  const openTab = useCallback((title: string, initialThread?: ThreadTurn[], dbId?: number | null, briefMeta?: BriefMeta | null) => {
    const existing = tabsRef.current.find((t) => t.title === title)
    if (existing) {
      setActiveTabId(existing.id)
      setDraft("")
      return existing.id
    }
    const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    setTabs((prev) => [...prev, {
      id, title, thread: initialThread ?? [], dbConvId: dbId ?? null,
      briefMeta: briefMeta ?? null, insightBody: null, prd: null, evidence: null,
      prdGenerating: false, evidenceGenerating: false,
    }])
    setActiveTabId(id)
    setDraft("")
    return id
  }, [])

  const closeTab = useCallback((tabId: string) => {
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== tabId)
      if (activeTabId === tabId) {
        setActiveTabId(next.length > 0 ? next[next.length - 1].id : null)
      }
      return next
    })
  }, [activeTabId])

  // ── Open a PRD as a NEW CHAT TAB with the content panel over it ─────────────
  // A "view/generate PRD" from another surface (brief cards, brief composer,
  // backlog) routes here via NavigationContext.openPrdTab → pendingPrdTab. We
  // spawn (or reuse, by title) a fresh chat tab, drive the requested source into
  // its cached PRD + the shared ContentContext, and flag the content panel to
  // slide open (deferred a commit so the route-change close can't swallow it).
  // The PRD/Evidence/Tickets all render in that panel — the tab itself is a
  // normal chat the user can keep talking in.
  const openPrdInTab = useCallback((req: PrdTabRequest) => {
    const { title, source } = req
    const meta = source.kind === "generateBacklog" ? null : source.meta
    const existing = tabsRef.current.find((t) => t.title === title)
    const tabId = existing?.id ?? `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    if (existing) {
      setActiveTabId(existing.id)
      // Backfill the insight body onto an already-open tab that lacks one (e.g. a
      // tab created before this field existed, or opened via a path that didn't
      // carry it) so reopening the insight surfaces its content, not just a title.
      if (req.insightBody && !existing.insightBody) {
        setTabs((prev) => prev.map((t) => t.id === existing.id ? { ...t, insightBody: req.insightBody ?? null } : t))
      }
    } else {
      setTabs((prev) => [...prev, {
        id: tabId, title, thread: [], dbConvId: null, briefMeta: meta,
        insightBody: req.insightBody ?? null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
      }])
      setActiveTabId(tabId)
    }
    setDraft("")
    setPrdPanelPending(true)

    // Reuse a PRD already cached on this tab (unless the caller handed us a fresh
    // one) — don't regenerate/re-fetch an already-open PRD.
    if (existing?.prd && source.kind !== "ready") {
      setContent({ prd: existing.prd, prdMeta: existing.briefMeta, prdGenerating: false })
      return
    }
    // Caller already holds the PRD — show it immediately, no async work.
    if (source.kind === "ready") {
      setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: source.prd, briefMeta: source.meta } : t))
      setContent({ prd: source.prd, prdMeta: source.meta, prdGenerating: false })
      return
    }
    // generate | generateBacklog | load — kick off, show the panel's spinner,
    // then land the result on the tab (and shared content while it's active).
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: null, briefMeta: meta, prdGenerating: true } : t))
    setContent({ prd: null, prdMeta: meta, prdGenerating: true })
    void (async () => {
      try {
        const result =
          source.kind === "generate" ? await runPrdGeneration(source.meta)
          : source.kind === "generateBacklog" ? await runPrdGenerationFromBacklog(source.backlogItemId)
          : await loadPrdById(source.prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: result.prd, prdGenerating: false } : t))
          if (activeTabIdRef.current === tabId) setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
        } else {
          setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prdGenerating: false } : t))
          if (activeTabIdRef.current === tabId) setContent({ prdGenerating: false })
          showToast("PRD unavailable", result.message.slice(0, 200))
        }
      } catch (e) {
        setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prdGenerating: false } : t))
        if (activeTabIdRef.current === tabId) setContent({ prdGenerating: false })
        showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
      }
    })()
  }, [setContent, showToast])

  // ── Per-tab artifact generation ──────────────────────────────────────────
  const handleOpenPrd = useCallback(async () => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.prdGenerating) return
    // Already generated (loaded on this tab) — sync to context and open panel.
    if (tab.prd) {
      setContent({ prd: tab.prd, prdMeta: tab.briefMeta })
      openContentPanel("prd")
      return
    }
    // A PRD already exists in the DB for this insight but isn't on the tab —
    // e.g. after a reload, where `prd` is stripped from the persisted tab. LOAD
    // the existing PRD by id; do NOT regenerate (that would spawn a duplicate and
    // burn a full generation). This is what makes the button "View PRD" open the
    // real doc rather than kick off a new build.
    if (chatInsightState?.hasPrd && chatInsightState.prdId != null) {
      const prdId = chatInsightState.prdId
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
      setContent({ prd: null, prdMeta: null, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await loadPrdById(prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd } : t))
          setContent({ prd: result.prd, prdMeta: tab.briefMeta, prdGenerating: false })
        } else {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
          setContent({ prdGenerating: false })
          showToast("Couldn't load PRD", result.message)
        }
      } catch (e) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
        setContent({ prdGenerating: false })
        showToast("Couldn't load PRD", e instanceof Error ? e.message : "Unknown error")
      }
      return
    }
    const defaultKey = pickDefaultDetailKey(content.briefDetails)
    const meta = tab.briefMeta
      ?? content.detail?.meta
      ?? (defaultKey ? content.briefDetails[defaultKey]?.meta ?? null : null)
    if (!meta) {
      openContentPanel("prd") // panel will show empty state / prompt
      return
    }
    setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
    // Drive the panel's generating spinner via content too (not just per-tab),
    // so the right rail shows in-progress PRD state immediately on open.
    setContent({ prd: null, prdMeta: null, prdGenerating: true })
    openContentPanel("prd")
    try {
      const result = await runPrdGeneration(meta)
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd } : t))
        setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
      } else {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
        setContent({ prdGenerating: false })
        showToast("PRD generation failed", result.message)
      }
    } catch (e) {
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
      setContent({ prdGenerating: false })
      showToast("PRD generation failed", e instanceof Error ? e.message : "Unknown error")
    }
  }, [activeTabId, chatInsightState, content.briefDetails, content.detail?.meta, openContentPanel, setContent, showToast])

  const handleOpenEvidence = useCallback(async () => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.evidenceGenerating) return
    // Already generated — sync to context and open panel
    if (tab.evidence) {
      setContent({ evidence: tab.evidence })
      openContentPanel("evidence")
      return
    }
    const defaultKey = pickDefaultDetailKey(content.briefDetails)
    const meta = tab.briefMeta
      ?? content.detail?.meta
      ?? (defaultKey ? content.briefDetails[defaultKey]?.meta ?? null : null)
    if (!meta) {
      openContentPanel("evidence")
      return
    }
    setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: true } : t))
    setContent({ evidence: null, evidenceGenerating: true })
    openContentPanel("evidence")
    try {
      const result = await runEvidenceGeneration(meta)
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false, evidence: result.evidence } : t))
        setContent({ evidence: result.evidence, evidenceGenerating: false })
      } else {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
        setContent({ evidenceGenerating: false })
        showToast("Evidence generation failed", result.message)
      }
    } catch (e) {
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
      setContent({ evidenceGenerating: false })
      showToast("Evidence generation failed", e instanceof Error ? e.message : "Unknown error")
    }
  }, [activeTabId, content.briefDetails, content.detail?.meta, openContentPanel, setContent, showToast])

  // ── Resume orphaned in-flight jobs on (re)mount ───────────────────────────
  // PRD / evidence generation kicks off a fire-and-forget server job; the only
  // client trace is an in-memory *Generating flag + an await closure. A remount
  // (tab backgrounded long enough to unmount, navigate away+back) drops that
  // closure and orphans the running job in the UI though the server finishes.
  // If a pending job id was persisted (jobResume), re-enter the visibility-aware
  // poll against the existing status endpoint — NOT generate again (the resume
  // helpers only GET). Runs once per active tab.
  const resumedTabsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    const meta = tab?.briefMeta
    if (!meta) return
    if (resumedTabsRef.current.has(activeTabId)) return
    resumedTabsRef.current.add(activeTabId)
    const scope = insightScope(meta.briefId, meta.insightIndex)

    const pendingPrd = getPendingJob("prd", "_", scope)
    if (pendingPrd && !tab?.prd && !tab?.prdGenerating) {
      const prdId = Number(pendingPrd.id)
      if (Number.isFinite(prdId)) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
        setContent({ prd: null, prdMeta: null, prdGenerating: true })
        void (async () => {
          try {
            const result = await resumePrdGeneration(prdId, meta)
            if (result.ok) {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd } : t))
              setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
            } else {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
              setContent({ prdGenerating: false })
            }
          } catch {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false } : t))
            setContent({ prdGenerating: false })
          }
        })()
      }
    }

    const pendingEvidence = getPendingJob("evidence", "_", scope)
    if (pendingEvidence && !tab?.evidence && !tab?.evidenceGenerating) {
      const evId = Number(pendingEvidence.id)
      if (Number.isFinite(evId)) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: true } : t))
        void (async () => {
          try {
            const result = await resumeEvidenceGeneration(evId, meta)
            if (result.ok) {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false, evidence: result.evidence } : t))
            } else {
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
            }
          } catch {
            setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false } : t))
          }
        })()
      }
    }
  }, [activeTabId, setContent])

  const conversations = content.conversations
  const starters = content.ondemandStarters
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations

  const profileName =
    auth.kind === "authed" ? profileDisplayName(profile, auth.user.email) : null
  const name =
    content.userName?.split(/\s+/)[0] ??
    profileName?.split(/\s+/)[0] ??
    "there"
  const userInitials = profileName
    ? profileName.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("")
    : name.slice(0, 1).toUpperCase()
  const homeCards = content.homeStarterCards.filter((c) => c.id !== "home-goto-ask")

  // When the active tab changes, sync its cached artifacts into ContentContext so
  // ContentPanel always shows the current tab's PRD / evidence.
  // We do NOT clear content.detail here — it holds the global brief finding context
  // that handleOpenPrd / handleOpenEvidence use as a fallback generation source.
  useEffect(() => {
    const tab = tabsRef.current.find((t) => t.id === activeTabId) ?? null
    setContent({
      prd: tab?.prd ?? null,
      prdMeta: tab?.briefMeta ?? null,
      evidence: tab?.evidence ?? null,
      // When switching tabs, reset the generating flag so the panel reflects
      // this tab's actual state (generating is tracked per-tab in local state).
      evidenceGenerating: tab?.evidenceGenerating ?? false,
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, setContent])

  useEffect(() => {
    if (!pendingSearchHandoff) return
    const { query, reply, convId } = pendingSearchHandoff
    setPendingSearchHandoff(null)
    const title = query.length > 40 ? `${query.slice(0, 37)}…` : query
    openTab(title, [{ id: convId, query, reply }])
    setActiveConv(0)
  }, [pendingSearchHandoff, setPendingSearchHandoff, openTab])

  useEffect(() => {
    if (pendingOndemandDraft == null || !pendingOndemandDraft.trim()) return
    const text = pendingOndemandDraft
    setPendingOndemandDraft(null)
    // If no active tab, pre-fill the composer; user hits Enter to send
    if (!activeTabId) {
      setDraft(text)
      requestAnimationFrame(() => {
        const ta = composerRef.current
        if (ta) {
          ta.style.height = "auto"
          ta.style.height = `${Math.min(ta.scrollHeight, 240)}px`
          ta.focus()
        }
      })
    } else {
      // Active tab exists — open a new tab with this as the first message
      const title = text.length > 40 ? `${text.slice(0, 37)}…` : text
      openTab(title)
      setDraft(text)
    }
  }, [pendingOndemandDraft, setPendingOndemandDraft, activeTabId, openTab])

  // ── Per-tab Supabase persistence ─────────────────────────────────────────
  // Each tab maps to its OWN conversation, tracked via ChatTab.dbConvId. The
  // persistence helper reads/writes that per-tab id (never a shared ref), so
  // parallel chats record into separate conversations. A single in-flight create
  // per tab keeps the user + assistant turns in ONE conversation under the
  // fire-and-forget timing (see chatPersistence.ts).
  const setTabConvId = useCallback((tabId: string, convId: number) => {
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, dbConvId: convId } : t))
  }, [])
  // Stable single instance — its per-tab in-flight-create map must persist across
  // renders, so we build it once (lazy ref init) rather than per render.
  const persistenceRef = useRef<ReturnType<typeof createChatPersistence> | null>(null)
  if (persistenceRef.current === null) {
    persistenceRef.current = createChatPersistence({
      getApi: () => import("../../../lib/api").then((m) => m.conversationsApi),
      getTabConvId: (tabId) => tabsRef.current.find((t) => t.id === tabId)?.dbConvId ?? null,
      setTabConvId: (tabId, convId) => setTabConvId(tabId, convId),
      onConversationCreated: (turnId, convId) => {
        // Tag the in-memory conversation with the DB id so the rail can load turns.
        const latest = conversationsRef.current
        const tagged = latest.map((c) =>
          c.id === turnId ? { ...c, _dbId: convId } as any : c,
        )
        setContent({ conversations: tagged })
      },
    })
  }
  const persistence = persistenceRef.current

  // Resume a conversation from ChatsScreen or BacklogScreen (loads turns)
  const checkResume = useCallback(() => {
    try {
      const raw = localStorage.getItem("sprntly_resume_conv")
      if (!raw) return
      localStorage.removeItem("sprntly_resume_conv")
      const data = JSON.parse(raw) as { dbId: number; title: string; turns: { role: string; content: string }[] }
      if (!data.turns || data.turns.length === 0) return
      // The resumed tab's dbConvId is set via openTab(..., data.dbId) below —
      // per-tab now, no shared ref.
      const restored: ThreadTurn[] = []
      for (let i = 0; i < data.turns.length; i++) {
        const t = data.turns[i]
        if (t.role === "user") {
          const next = data.turns[i + 1]
          const reply = next?.role === "assistant" ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse : undefined
          restored.push({ id: `resumed-${i}`, query: t.content, reply })
          if (reply) i++
        }
      }
      if (restored.length > 0) {
        openTab(data.title || "Resumed chat", restored, data.dbId)
        setActiveConv(0)
      }
    } catch { /* ignore corrupt data */ }
  }, [openTab])
  // Check on mount + whenever we navigate to this screen
  useEffect(() => { checkResume() }, [checkResume])
  // Re-check when the route lands on chat (covers goTo("chat") from ChatsScreen)
  useEffect(() => {
    if (currentScreen === "chat") {
      // Small delay to let localStorage write from ChatsScreen settle
      const t = setTimeout(checkResume, 50)
      return () => clearTimeout(t)
    }
  }, [currentScreen, checkResume])

  // The brief is the pinned first tab of this surface. When the route lands on
  // the brief screen (sidebar "Weekly brief" → goTo("brief") → /brief, which
  // also renders ChatScreen), activate the pinned brief tab — even if the surface
  // was already mounted on a chat tab.
  useEffect(() => {
    if (currentScreen === "brief") {
      setActiveTabId(BRIEF_TAB_ID)
      setDraft("")
    }
  }, [currentScreen])

  const pushPendingConversation = useCallback(
    (turnId: string, query: string, targetTabId: string) => {
      const prev = conversationsRef.current
      const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
      const timeStr = new Date().toISOString()
      const nextCount = prev.length + 1
      setContent({
        conversations: [
          { id: turnId, title, time: timeStr, savedTurn: { id: turnId, query } },
          ...prev,
        ],
        sidebarConvCount: nextCount,
      })
      // Persist to Supabase against THIS tab's conversation (create-once per tab).
      // Fire-and-forget — failures are swallowed inside the helper.
      void persistence.pushUserTurn(targetTabId, { turnId, title, query })
    },
    [setContent, persistence],
  )

  const finalizeConversationTurn = useCallback(
    (turnId: string, updates: { reply?: AskResponse; error?: string }, targetTabId: string) => {
      const prev = conversationsRef.current
      setContent({
        conversations: prev.map((c) => {
          if (c.id !== turnId || !c.savedTurn) return c
          const base = { id: turnId, query: c.savedTurn.query }
          if (updates.reply !== undefined) {
            return { ...c, savedTurn: { ...base, reply: updates.reply } }
          }
          if (updates.error !== undefined) {
            return { ...c, savedTurn: { ...base, error: updates.error } }
          }
          return c
        }),
      })
      // Save assistant reply as a turn in this tab's Supabase conversation.
      // The helper awaits any in-flight create so the assistant turn lands in the
      // SAME conversation as its user turn.
      if (updates.reply) {
        void persistence.pushAssistantTurn(targetTabId, replyToText(updates.reply))
      }
    },
    [setContent, persistence],
  )

  // "Generate a PRD …" is a COMMAND, not a conversation: it opens the PRD as its
  // OWN chat tab (with the Evidence/PRD/Tickets panel), never as a chat message.
  // Without this the ask agent routes it to the prd-author skill and answers with
  // a raw HTML document dumped into the chat bubble. Mirror BriefChat's prdFlow:
  // resolve the current brief's top insight (index 0) and hand off via openPrdTab.
  // openPrdTab's generate path is find-or-create (POST /v1/prd/generate reuses an
  // existing DB PRD when one exists), so an already-generated PRD is served from
  // the DB rather than regenerated.
  const prdCommandFlow = useCallback(async () => {
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        showToast("No brief yet", "Run the pipeline to refresh this week's brief first.")
        return
      }
      openPrdTab({
        title: "PRD · Weekly brief",
        source: { kind: "generate", meta: { briefId: brief.id, insightIndex: 0 } },
      })
    } catch (e) {
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    }
  }, [activeCompany, openPrdTab, showToast])

  const submitAsk = useCallback(
    async (rawQuery: string) => {
      // A "generate a PRD" phrasing is a command — open the PRD tab from the
      // brief's top insight instead of sending it to the ask agent (which would
      // answer with a raw prd-author HTML dump). Intercept before any tab/ask work.
      if (isPrdCommand(rawQuery.trim())) {
        void prdCommandFlow()
        return
      }
      // Append attached file content as context
      let query = rawQuery.trim()
      if (attachments.length > 0) {
        const ctx = attachments.map((a) => `--- ${a.name} ---\n${a.content}`).join("\n\n")
        query = `${query}\n\n[Attached files]\n${ctx}`
        setAttachments([]) // clear after sending
      }
      if (query.length < 1) return
      // Early cheap guard: if the ACTIVE tab already has an ask in flight, bail
      // before doing any work. (Authoritative per-tab guard happens once
      // targetTabId is resolved below — needed for the no-active-tab case where
      // openTab creates the target.)
      if (activeTabId != null && askingTabsRef.current.has(activeTabId)) return
      const id =
        typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      // Capture the target tab ID up-front so async callbacks always write to
      // the right tab, even if the user switches tabs while the request is in-flight.
      let targetTabId: string
      // No active tab, OR the active "tab" is the synthetic, thread-less brief
      // tab → spawn a FRESH chat tab seeded with the query. A chat started from
      // the weekly brief must never thread inline into it (the brief tab carries
      // no `tabs` entry, so appending would silently no-op anyway).
      if (!activeTabId || activeTabId === BRIEF_TAB_ID) {
        const title = query.length > 40 ? `${query.slice(0, 37)}…` : query
        targetTabId = openTab(title, [{ id, query }])
      } else {
        targetTabId = activeTabId
        const newTitle = query.length > 40 ? `${query.slice(0, 37)}…` : query
        setTabs((prev) => prev.map((t) => {
          if (t.id !== targetTabId) return t
          // First message in a placeholder "New chat" tab → give it the real
          // title from the query (rename in place; do NOT spawn a second tab).
          const title = t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? newTitle : t.title
          return { ...t, title, thread: [...t.thread, { id, query }] }
        }))
      }
      pushPendingConversation(id, query, targetTabId)
      setActiveConv(0)
      // runTabAsk holds the AUTHORITATIVE per-tab in-flight guard + busy marking.
      // It returns false (running nothing) if this tab already has an ask in
      // flight; otherwise it runs askApi.ask CONCURRENTLY with other tabs' asks
      // and routes the reply/error to the captured targetTabId. The guard, busy
      // toggling, and cleanup (even if the tab is closed mid-flight) all live in
      // the helper so the concurrency contract is unit-tested in one place.
      await runTabAsk({
        targetTabId,
        asking: askingTabsRef.current,
        setBusy: setBusyTabs,
        // Fire-and-forget + poll: POST returns an ask_id, the answer keeps
        // generating server-side, and the active ask_id is persisted per tab
        // (jobResume) so a backgrounded/remounted tab re-attaches via the mount
        // resume effect instead of re-asking.
        ask: () => runAskGeneration(query, activeCompany, targetTabId, { isCancelled: () => !mountedRef.current }),
        onResult: (tabId, res) => {
          setTabs((prev) => prev.map((t) =>
            t.id !== tabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === id ? { ...turn, reply: res } : turn)
            }
          ))
          finalizeConversationTurn(id, { reply: res }, tabId)
        },
        onError: (tabId, e) => {
          // Poll cancelled because the user left the chat screen mid-flight: the
          // ask_id is still persisted, so the mount-time resume effect will
          // re-attach and populate on return. Not a failure — no error UI/toast.
          if (e instanceof AskCancelledError) return
          const detail = e instanceof ApiError && e.body && typeof e.body === "object" && "detail" in e.body
            ? (e.body as { detail: unknown }).detail
            : null
          const detailStr =
            typeof detail === "string"
              ? detail
              : Array.isArray(detail)
                ? detail
                  .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
                  .join(" · ")
                : null
          const msg =
            e instanceof ApiError
              ? detailStr || e.message
              : e instanceof Error
                ? e.message
                : "Something went wrong"
          setTabs((prev) => prev.map((t) =>
            t.id !== tabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === id ? { ...turn, error: msg } : turn)
            }
          ))
          finalizeConversationTurn(id, { error: msg }, tabId)
          showToast("Ask failed", msg.slice(0, 120))
        },
      })
    },
    [activeCompany, activeTabId, attachments, finalizeConversationTurn, openTab, prdCommandFlow, pushPendingConversation, showToast],
  )

  // ── Brief → new chat tab hand-off ─────────────────────────────────────────
  // A question typed on the weekly-brief surface must open its OWN chat tab, not
  // thread inline into the brief. BriefChat sets pendingChatHandoff; we consume
  // it once here by running it through submitAsk. With the brief tab active (the
  // only place this fires), submitAsk spawns a fresh tab seeded with the query —
  // so every chat started from the brief lands in a new tab.
  useEffect(() => {
    if (!pendingChatHandoff) return
    const { query } = pendingChatHandoff
    setPendingChatHandoff(null)
    void submitAsk(query)
  }, [pendingChatHandoff, setPendingChatHandoff, submitAsk])

  // ── PRD → new chat tab hand-off ───────────────────────────────────────────
  // A "view/generate PRD" from another surface (brief cards, brief composer,
  // backlog) fills pendingPrdTab via openPrdTab and routes to `/`. Consume it
  // once — openPrdInTab spawns the chat tab, drives the source, and flags the
  // content panel to open over it.
  useEffect(() => {
    if (!pendingPrdTab) return
    const req = pendingPrdTab
    setPendingPrdTab(null)
    openPrdInTab(req)
  }, [pendingPrdTab, setPendingPrdTab, openPrdInTab])

  // Slide the content panel open on the commit AFTER openPrdInTab flags it. The
  // deferral matters when the PRD was opened from another surface: openPrdTab
  // routes to `/`, and NavigationContext closes the panel on that route change —
  // opening it here a commit later (route now settled) survives that close.
  useEffect(() => {
    if (!prdPanelPending) return
    setPrdPanelPending(false)
    openContentPanel("prd")
  }, [prdPanelPending, openContentPanel])

  // Keep the content panel scoped to the tab that owns it. The panel is a single
  // global overlay; "View PRD" on a brief finding spawns a PRD chat tab and slides
  // the panel open over it (wanted). But because the panel is global, switching to
  // ANOTHER tab that has no PRD of its own — the pinned brief tab, or a fresh "New
  // chat" — would leave it hanging there (not wanted). So on an actual tab switch,
  // close a lingering panel unless the tab we land on owns a PRD (already loaded
  // or mid-generation) or a PRD open is imminent (prdPanelPending, set by
  // openPrdInTab a commit before it opens the panel). Guarded on the switch, so
  // the brief's own inline actions (Tickets / Evidence / multi-agent — which open
  // the panel WITHOUT a tab switch) are untouched and stay visible.
  const prevTabForPanelRef = useRef(activeTabId)
  useEffect(() => {
    const switchedTab = prevTabForPanelRef.current !== activeTabId
    prevTabForPanelRef.current = activeTabId
    if (!switchedTab || !contentPanelTab || prdPanelPending) return
    if (isBriefTab) { closeContentPanel(); return }
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab?.prd && !tab?.prdGenerating) closeContentPanel()
  }, [activeTabId, isBriefTab, contentPanelTab, prdPanelPending, closeContentPanel])

  // ── Restore the PRD panel after a reload ───────────────────────────────────
  // Tabs persist across reloads (localStorage) but their cached `prd` does NOT —
  // it's stripped to keep storage small (see the slim persist above). So a reload
  // that lands back on a PRD-bound chat tab used to show the tab with the panel
  // CLOSED, forcing a manual "View PRD" click. Here we reopen it automatically:
  // once the brief prototype map resolves and confirms a PRD exists in the DB for
  // the ACTIVE tab's insight, open the panel and LOAD the saved PRD by id
  // (handleOpenPrd takes the DB-load branch — never a regeneration).
  //
  // Keyed on the active tab (not a captured mount tab): `activeCompany` resolves
  // asynchronously and the company-change effect re-seeds the active tab a commit
  // or two after mount, so "the tab we reloaded onto" isn't known at first render.
  //
  // We act ONLY on a positive signal — the map has resolved AND reports a DB PRD
  // for this insight — and mark the tab handled ONLY when we actually open it.
  // This matters because useBriefPrototypeMap starts `loading:false` with an empty
  // map and only flips to `loading:true` inside its own effect (a commit later):
  // an earlier design that gave up whenever `hasPrd` was false would latch onto
  // that empty pre-fetch window and never restore. Here a false/empty reading is
  // simply "not yet" — we wait for a later render. Guards keep the panel off the
  // wrong surface:
  //   • Never the brief tab, and never a plain (non-PRD) chat → a reload (or
  //     switch) onto a new chat leaves the panel closed.
  //   • Skips when the tab already holds/loads a PRD or a panel is already open
  //     (openPrdInTab / a manual open handled it) — and once opened, `tab.prd` is
  //     cached, so a manual panel-close is never undone.
  //   • Fires at most once per tab (autoRestoredTabsRef).
  const autoRestoredTabsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!activeTabId || isBriefTab) return
    if (autoRestoredTabsRef.current.has(activeTabId)) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    // Not a PRD-bound tab, already loaded/loading, or a panel is already open →
    // nothing to restore right now (don't latch; conditions above are transient).
    if (!tab || !tab.briefMeta || tab.prd || tab.prdGenerating || contentPanelTab) return
    // Only a CONFIRMED DB PRD triggers the restore. A not-yet-resolved map reads as
    // hasPrd=false → treat as "wait", not "give up", and re-check on the next render.
    if (!(chatInsightState?.hasPrd && chatInsightState.prdId != null)) return
    autoRestoredTabsRef.current.add(activeTabId)
    void handleOpenPrd()
  }, [activeTabId, isBriefTab, contentPanelTab, chatInsightState, handleOpenPrd])

  // ── Resume orphaned in-flight ASK jobs on (re)mount ───────────────────────
  // A chat Ask is fire-and-forget: POST returns an ask_id and the answer keeps
  // generating server-side. The pending USER turn lives in the persisted
  // tab.thread (so the question survives a remount), but the awaiting poll
  // closure + the in-memory asking/busy markers do NOT. If a pending ask_id was
  // persisted (jobResume), re-enter the visibility-aware poll against the
  // existing status endpoint — NOT re-POST — and re-show the "asking…" state.
  // Runs once per tab per mount.
  const resumedAskTabsRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    for (const tab of tabsRef.current) {
      if (resumedAskTabsRef.current.has(tab.id)) continue
      const pending = getPendingAsk(activeCompany, tab.id)
      if (!pending) continue
      const askId = Number(pending.id)
      if (!Number.isFinite(askId)) continue
      // Re-attach only when the last turn is still awaiting a reply (the
      // canonical "asking…" marker that survives in the persisted thread).
      const last = tab.thread[tab.thread.length - 1]
      if (!last || last.reply !== undefined || last.error !== undefined) continue
      if (askingTabsRef.current.has(tab.id)) continue
      resumedAskTabsRef.current.add(tab.id)
      const turnId = last.id
      const targetTabId = tab.id
      // Restore the optimistic asking/busy UX for this tab.
      askingTabsRef.current.add(targetTabId)
      setBusyTabs((prev) => addToSet(prev, targetTabId))
      void (async () => {
        try {
          const res = await resumeAskGeneration(askId, activeCompany, targetTabId, () => !mountedRef.current)
          setTabs((prev) => prev.map((t) =>
            t.id !== targetTabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === turnId ? { ...turn, reply: res } : turn),
            }
          ))
          finalizeConversationTurn(turnId, { reply: res }, targetTabId)
        } catch (e) {
          // Unmounted again mid-resume: leave the marker so the NEXT mount
          // re-attaches. Don't write an error into the thread.
          if (e instanceof AskCancelledError) return
          const msg = e instanceof Error ? e.message : "Something went wrong"
          setTabs((prev) => prev.map((t) =>
            t.id !== targetTabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === turnId ? { ...turn, error: msg } : turn),
            }
          ))
          finalizeConversationTurn(turnId, { error: msg }, targetTabId)
        } finally {
          askingTabsRef.current.delete(targetTabId)
          setBusyTabs((prev) => removeFromSet(prev, targetTabId))
        }
      })()
    }
  }, [activeCompany, finalizeConversationTurn])

  const handleComposerSubmit = () => {
    const q = draft.trim()
    // Cheap active-tab guard; submitAsk re-checks per the resolved target tab.
    if (q.length < 1 || (activeTabId != null && askingTabsRef.current.has(activeTabId))) return
    setDraft("")
    void submitAsk(q)
    const ta = composerRef.current
    if (ta) {
      ta.style.height = "auto"
      ta.style.height = "24px"
    }
  }

  const handleComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleComposerSubmit()
    }
  }

  const handleComposerInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value
    setDraft(val)
    e.target.style.height = "auto"
    e.target.style.height = Math.min(e.target.scrollHeight, 240) + "px"
    // Slash command detection: show dropdown when text starts with /
    if (val.startsWith("/")) {
      setShowSlash(true)
      setSlashFilter(val.slice(1).toLowerCase())
    } else {
      setShowSlash(false)
    }
  }

  const handleSlashSelect = (skill: SkillInfo) => {
    setShowSlash(false)
    setDraft(skill.trigger + " ")
    composerRef.current?.focus()
  }

  const filteredSkills = skills.filter((s) =>
    slashFilter === "" ||
    s.trigger.toLowerCase().includes("/" + slashFilter) ||
    s.label.toLowerCase().includes(slashFilter) ||
    s.description.toLowerCase().includes(slashFilter)
  )

  const handleStarterChip = (text: string) => {
    void submitAsk(text)
  }

  const handleHomeCard = (c: ChatHomeCard) => {
    if (c.target === "ondemand" && c.prompt) {
      setPendingOndemandDraft(c.prompt)
      return
    }
    if (c.target === "ondemand") {
      goTo("chat")
      return
    }
    if (c.target === "brief" && c.prompt) {
      setAIBarValue(c.prompt)
      goTo("brief")
      expandAiPanel()
      return
    }
    goTo(c.target)
  }

  const startNewThread = useCallback(() => {
    // "+" behaves like a real new browser tab: it must create a VISIBLE, ACTIVE
    // tab chip in the strip (so the user sees they're on a new tab and can switch
    // back) — not a tab-less landing. Reuse-or-create: if an empty "New chat" tab
    // already exists (no messages), just activate it rather than piling up
    // duplicates. We still prune OTHER empty tabs (keep the strip clean) but never
    // the one the user is about to sit on.
    let targetId: string | null = null
    setTabs((prev) => {
      const existingEmpty = prev.find((t) => t.thread.length === 0 && t.title === NEW_CHAT_TITLE)
      if (existingEmpty) {
        targetId = existingEmpty.id
        // Drop any OTHER empty tabs, keep the one we're reusing.
        return prev.filter((t) => t.thread.length > 0 || t.id === existingEmpty.id)
      }
      const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      targetId = id
      // Prune other empty tabs, then append the fresh "New chat" tab.
      const kept = prev.filter((t) => t.thread.length > 0)
      return [...kept, {
        id, title: NEW_CHAT_TITLE, thread: [], dbConvId: null, briefMeta: null,
        insightBody: null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
      }]
    })
    setActiveTabId(targetId)
    setDraft("")
    setActiveConv(null)
    // No shared conv-id to reset — each tab tracks its own dbConvId.
  }, [])

  // ── "New chat" hand-off (`/?new=1`) ───────────────────────────────────────
  // The sidebar's "New chat" affordance pushes `/?new=1` (goToNewChat). The home
  // surface otherwise DEFAULTS to the pinned brief tab on a fresh load, so this
  // one-shot param is what makes "New chat" reliably land on a fresh chat landing
  // instead of the brief. We start a new thread, then strip the param via
  // router.replace so a later refresh doesn't re-open a new chat. Works whether
  // the surface is freshly mounted (param present on first render) or already on
  // screen (the param change re-runs this effect).
  //
  // `consumedNewRef` guards against re-consuming while the param is still present:
  // `useSearchParams()` can hand back a fresh object each render (and startNewThread
  // itself re-renders), so without the latch the effect would loop. It re-arms when
  // the param is absent, so a *subsequent* `/?new=1` nav fires a fresh new-chat.
  const consumedNewRef = useRef(false)
  useEffect(() => {
    const hasNew = searchParams.get("new") != null
    if (!hasNew) {
      consumedNewRef.current = false
      return
    }
    if (consumedNewRef.current) return
    consumedNewRef.current = true
    startNewThread()
    router.replace("/")
  }, [searchParams, startNewThread, router])

  const hasThread = thread.length > 0
  // A tab bound to a PRD or brief insight opens with the insight itself as the
  // conversation's first agent message (see the insight turn rendered at the top
  // of the thread) — NOT as a pinned heading above the chat. That message is what
  // anchors the chat to its insight and hosts the Generate/View PRD + prototype
  // actions, so an insight-bound tab always shows the thread view (never the
  // generic "Welcome back" landing) even before the user has sent anything.
  const showInsightMsg = !!(activeTab?.prd || activeTab?.briefMeta)
  const showThreadView = hasThread || showInsightMsg
  // The tab title is "PRD · <insight>"; the message shows the insight sentence on
  // its own (the "PRD" kind is already a chip), so strip the redundant prefix.
  const insightText = (activeTab?.prd?.title ?? activeTab?.title ?? "").replace(/^PRD · /, "")
  // The insight's body/description (from the originating brief finding), shown
  // under the title so the opening card carries the finding's content, not just
  // its heading. Null for tabs not opened from a finding (backlog / plain chat).
  const insightBody = activeTab?.insightBody ?? null
  // Whether a PRD exists for this tab's insight — either loaded on the tab OR
  // saved in the DB (via the brief-prototype map). The tab's `prd` is dropped
  // from localStorage on reload, so relying on it alone made the CTA say
  // "Generate PRD" for an insight that already has one; the DB signal keeps the
  // label ("View PRD") and the action (load, not regenerate) correct after reload.
  const chatPrdExists = !!activeTab?.prd || !!(chatInsightState?.hasPrd && chatInsightState.prdId != null)
  // While the brief-prototype map is still loading we don't yet KNOW whether a
  // PRD exists, so committing to "Generate PRD" would flash the wrong label then
  // flip to "View PRD" once the map lands. Show a neutral "Loading…" until we
  // know — but only for an insight-bound tab that has no PRD loaded on it yet
  // (a tab already carrying its prd is authoritative, no wait needed).
  const chatPrdCtaWaiting = !chatPrdExists && !!activeTab?.briefMeta && chatMapLoading
  const displayChips = useMemo(() => {
    const chips = buildHomeChips(homeCards, starters)
    return chips.length > 0 ? chips : DEFAULT_HOME_CHIPS
  }, [homeCards, starters])
  const showChipRow = !hasThread
  const showEmptyStarters = false

  return (
    <AppLayout
      mainClassName="main--home-chat"
      mainStyle={{
        maxWidth: "none",
        padding: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        flex: "1 1 auto",
      }}
    >
      <div className="home-chat-root">
        <div className={`od-layout ${railExpanded ? "rail-expanded" : ""}`}>

          {/* Tab bar — always visible */}
          <div data-testid="chat-tab-bar" style={{
            display: "flex", alignItems: "stretch", gap: 0,
            borderBottom: "1px solid var(--line, #E8E6E0)", background: "var(--surface, #fff)",
            height: 40, overflowX: "auto", overflowY: "visible", flexShrink: 0,
          }}>
            {/* Pinned brief tab — always first, never closable (synthesized, not
                in `tabs`/localStorage). Selecting it renders <BriefChat/> below. */}
            <div
              key={BRIEF_TAB_ID}
              onClick={() => { setActiveTabId(BRIEF_TAB_ID); setDraft("") }}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "0 14px", fontSize: 13, cursor: "pointer",
                color: isBriefTab ? "var(--ink, #1A1A17)" : "var(--ink-3, #8C8A84)",
                fontWeight: isBriefTab ? 500 : 400,
                borderBottom: isBriefTab ? "2px solid var(--ink, #1A1A17)" : "2px solid transparent",
                marginBottom: -1,
                whiteSpace: "nowrap", transition: "color 0.12s, border-color 0.12s",
                userSelect: "none", flexShrink: 0,
              }}
            >
              <span style={{ lineHeight: "1.3" }}>Weekly brief</span>
            </div>
            {tabs.map((tab) => {
              const isActive = activeTabId === tab.id
              return (
                <div
                  key={tab.id}
                  onClick={() => { setActiveTabId(tab.id); setDraft("") }}
                  style={{
                    display: "flex", alignItems: "center", gap: 6,
                    padding: "0 10px 0 14px", fontSize: 13, cursor: "pointer",
                    color: isActive ? "var(--ink, #1A1A17)" : "var(--ink-3, #8C8A84)",
                    fontWeight: isActive ? 500 : 400,
                    borderBottom: isActive ? "2px solid var(--ink, #1A1A17)" : "2px solid transparent",
                    marginBottom: -1,
                    whiteSpace: "nowrap", transition: "color 0.12s, border-color 0.12s",
                    userSelect: "none", flexShrink: 0,
                  }}
                >
                  <span style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", lineHeight: "1.3" }}>
                    {tab.title}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); closeTab(tab.id) }}
                    style={{
                      display: "flex", alignItems: "center", justifyContent: "center",
                      width: 16, height: 16, flexShrink: 0,
                      background: "none", border: "none", cursor: "pointer",
                      fontSize: 13, color: "var(--ink-4, #B0AEA6)", padding: 0, lineHeight: 1,
                      borderRadius: 3,
                    }}
                    title="Close tab"
                  >×</button>
                </div>
              )
            })}
            {/* New-tab button — styled like Chrome's: a small rounded control
                just to the right of the last tab, vertically centered in the
                strip, with a subtle circular highlight on hover. */}
            <button
              type="button"
              onClick={startNewThread}
              aria-label="New chat"
              title="New chat"
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-2, #F1EFEA)" }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent" }}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                width: 28, height: 28, margin: "6px 4px 0 6px", padding: 0,
                background: "transparent", border: "none", cursor: "pointer",
                borderRadius: "50%", fontSize: 18, lineHeight: 1,
                color: "var(--ink-3, #8C8A84)", flexShrink: 0,
                transition: "background 0.12s",
              }}
            >+</button>
          </div>

          {isBriefTab ? (
            // Pinned brief tab → the full weekly-brief surface. ChatScreen already
            // provides AppLayout, so BriefChat renders bare (it owns its own
            // header + finding cards + composer + content-panel wiring).
            <BriefChat />
          ) : (
          <main className={`od-center ${showThreadView ? "od-center--thread" : "od-center--landing"}`}>
            <div className={`od-center-scroll${!showThreadView ? " od-center-scroll--home-landing" : ""}`}>
              {!showThreadView ? (
                <div className="home-landing-eyeline">
                  <div className="od-center-inner od-center-inner--home">
                    <div className="chat-greeting">
                      <h1 className="chat-greeting-title">
                        Welcome back, <em>{name}</em>.
                      </h1>
                      <p className="chat-greeting-sub">
                        Welcome to Sprntly — what would you like to work on?
                      </p>
                    </div>

                    <div className="home-landing-composer">
                      <div className="chat-home-composer" style={{ position: "relative" }}>
                        {/* Slash command dropdown (home) */}
                        {showSlash && filteredSkills.length > 0 && (
                          <div style={{
                            position: "absolute", bottom: "100%", left: 0, right: 0,
                            background: "var(--surface, #fff)", borderRadius: 10,
                            border: "1px solid var(--line, #E8E6E0)",
                            boxShadow: "0 -4px 20px rgba(0,0,0,0.08)", zIndex: 10,
                            maxHeight: 280, overflowY: "auto", padding: "6px 0",
                          }}>
                            <div style={{ padding: "4px 12px 6px", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--ink-4)" }}>
                              Skills
                            </div>
                            {filteredSkills.map((s) => (
                              <button
                                key={s.id}
                                type="button"
                                onClick={() => handleSlashSelect(s)}
                                style={{
                                  display: "flex", alignItems: "flex-start", gap: 10, width: "100%",
                                  padding: "8px 12px", background: "none", border: "none",
                                  cursor: "pointer", textAlign: "left", fontSize: 13,
                                }}
                                onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-2, #F4F1EA)" }}
                                onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "none" }}
                              >
                                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent, #179463)", fontFamily: "var(--font-mono, monospace)", minWidth: 80, flexShrink: 0 }}>
                                  {s.trigger}
                                </span>
                                <span>
                                  <span style={{ fontWeight: 500, color: "var(--ink)" }}>{s.label}</span>
                                  <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-3)", marginTop: 1 }}>{s.description}</span>
                                </span>
                              </button>
                            ))}
                          </div>
                        )}
                        <textarea
                          ref={composerRef}
                          className="chat-home-composer-input"
                          placeholder="Ask Sprntly anything, or type / for skills…"
                          rows={1}
                          value={draft}
                          onChange={handleComposerInput}
                          onKeyDown={handleComposerKeyDown}
                        />
                        <div className="chat-home-composer-footer">
                          <div className="chat-home-composer-actions">
                            <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.doc,.docx" style={{ display: "none" }} onChange={handleFileSelect} />
                            <button type="button" className="chat-home-action-btn" aria-label="Attach file" onClick={() => fileInputRef.current?.click()}>
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                              </svg>
                              Attach
                            </button>
                          </div>
                          <button
                            type="button"
                            className="chat-home-composer-send"
                            aria-label="Send"
                            disabled={busy || draft.trim().length < 1}
                            onClick={handleComposerSubmit}
                          >
                            <IconSendUp size={16} />
                          </button>
                        </div>
                      </div>
                      {showChipRow ? (
                        <div className="home-chip-row home-chip-row--under-chat" role="list">
                          {displayChips.map(({ kind, card }) => (
                            <button
                              key={`${kind}-${card.id}`}
                              type="button"
                              className={`home-chip${kind === "starter" ? " home-chip--muted" : ""}`}
                              role="listitem"
                              onClick={() =>
                                kind === "home"
                                  ? handleHomeCard(card)
                                  : handleStarterChip(card.prompt ?? card.title)
                              }
                            >
                              <span className="home-chip-icon" aria-hidden>
                                <ChatSuggestionIcon id={card.icon} size={16} />
                              </span>
                              <span className="home-chip-label">{card.title}</span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>

                    {showEmptyStarters ? (
                      <EmptyPane
                        title="No starter prompts yet"
                        hint="Populate `homeStarterCards` and `ondemandStarters` from your API or org defaults."
                        placeholders={4}
                      />
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="bc-scroll">
                  <div className="bc-thread">
                    {/* Insight message — the chat opens with its insight as the
                        agent's first message (in the flow, not a pinned heading).
                        Hosts the Generate/View PRD + Generate/View Prototype
                        actions, which relabel to "View …" once the artifact is
                        saved (PRD: loaded on the tab; prototype: ready in the DB
                        via the brief-prototype map). */}
                    {showInsightMsg ? (
                      <div className="bc-turn bc-turn--insight" data-testid="chat-insight-msg">
                        <div className="bc-agent-head">
                          <span className="bc-agent-mark">
                            <IconSparkle size={14} />
                          </span>
                          <span className="bc-agent-name">{AGENT_NAME}</span>
                          <span className="bc-agent-badge">
                            <IconSparkle size={10} />
                            PM COWORKER
                          </span>
                        </div>
                        <div className="bc-agent-body">
                          <div className="bc-insight-msg">
                            <span className="bc-insight-msg-kind">PRD</span>
                            <span className="bc-insight-msg-text">{insightText}</span>
                          </div>
                          {/* Insight body — the finding's content under the heading.
                              Rendered as markdown so LLM-supplied **bold** shows. */}
                          {insightBody ? (
                            <div className="bc-insight-msg-body fc-body--md">
                              <ReactMarkdown remarkPlugins={[remarkGfm]}>{insightBody}</ReactMarkdown>
                            </div>
                          ) : null}
                        </div>
                        <div className="bc-actions">
                          <button
                            type="button"
                            className="bc-action-btn bc-action-btn--primary"
                            disabled={!!activeTab?.prdGenerating || chatPrdCtaWaiting}
                            onClick={handleOpenPrd}
                          >
                            {activeTab?.prdGenerating
                              ? "Generating PRD…"
                              : chatPrdCtaWaiting ? "Loading…"
                              : chatPrdExists ? "View PRD" : "Generate PRD"}
                          </button>
                          <button
                            type="button"
                            className="bc-action-btn"
                            onClick={handleChatPrototype}
                          >
                            {prototypeCtaLabel(chatInsightState)}
                          </button>
                        </div>
                      </div>
                    ) : null}
                    {/* "User input needed" items from the PRD, surfaced as chat
                        messages with answer buttons. Answering patches only the
                        affected PRD sections and refreshes the panel live. */}
                    {activeTab?.prd ? (
                      <PrdInputQuestions
                        prdId={activeTab.prd.prd_id}
                        onPrdUpdated={handleInputPrdUpdated}
                      />
                    ) : null}
                    {thread.map((turn, idx) => {
                      const isLast = idx === thread.length - 1
                      const hasFreshReply = !!turn.reply && !animatedTurnIds.current.has(turn.id)
                      if (hasFreshReply) animatedTurnIds.current.add(turn.id)
                      return (
                        <div key={turn.id} className="bc-turn">
                          <div className="bc-user-head">
                            <span className="bc-avatar">{userInitials}</span>
                            <span className="bc-user-name">{name}</span>
                          </div>
                          <div className="bc-user-bubble">{turn.query}</div>
                          <div className="bc-agent-head">
                            <span className="bc-agent-mark">
                              <IconSparkle size={14} />
                            </span>
                            <span className="bc-agent-name">{AGENT_NAME}</span>
                            <span className="bc-agent-badge">
                              <IconSparkle size={10} />
                              PM COWORKER
                            </span>
                            {!turn.reply && !turn.error ? (
                              <span className="bc-agent-status">thinking…</span>
                            ) : null}
                          </div>
                          <div className="bc-agent-body">
                            {turn.error ? <div className="bc-error">{turn.error}</div> : null}
                            {!turn.reply && !turn.error ? <AssistantThinkingSkeleton compact /> : null}
                            {turn.reply ? (
                              <AskReplyBody
                                reply={turn.reply}
                                animateIn={hasFreshReply}
                                simulateTyping={hasFreshReply}
                              />
                            ) : null}
                          </div>
                          {isLast && turn.reply ? (
                            <div className="bc-actions">
                              <button
                                type="button"
                                className="bc-action-btn bc-action-btn--primary"
                                disabled={!!activeTab?.prdGenerating || chatPrdCtaWaiting}
                                onClick={handleOpenPrd}
                              >
                                {activeTab?.prdGenerating
                                  ? "Generating PRD…"
                                  : chatPrdCtaWaiting ? "Loading…"
                                  : chatPrdExists ? "View PRD" : "Generate PRD"}
                              </button>
                              <button
                                type="button"
                                className="bc-action-btn"
                                disabled={!!activeTab?.prdGenerating || !activeTab?.prd}
                                onClick={() => {
                                  if (activeTab?.prd) {
                                    setContent({ prd: activeTab.prd, prdMeta: activeTab.briefMeta })
                                    openContentPanel("tickets")
                                  }
                                }}
                                title={!activeTab?.prd ? "Generate a PRD first" : undefined}
                              >
                                Create tickets
                              </button>
                              <button
                                type="button"
                                className="bc-action-btn"
                                disabled={!!activeTab?.evidenceGenerating}
                                onClick={handleOpenEvidence}
                              >
                                {activeTab?.evidenceGenerating
                                  ? "Generating…"
                                  : activeTab?.evidence ? "Open evidence" : "View evidence"}
                              </button>
                              <button
                                type="button"
                                className="bc-action-btn"
                                onClick={handleChatPrototype}
                              >
                                {prototypeCtaLabel(chatInsightState)}
                              </button>
                            </div>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* The composer renders whenever the thread view is shown — including
                an insight-bound tab whose thread is still empty (opened from the
                brief/backlog): the user must be able to talk to Sprntly about that
                PRD right away. `hasThread` alone hid it there; `showThreadView`
                (hasThread || an insight message) restores it. A plain empty chat
                still uses the landing composer (showThreadView is false), so
                there's never a double composer. */}
            {showThreadView ? (
              <div className="bc-dock">
                {/* Floating "Create ticket" chip — only when the PRD rail is open
                    (it generates tickets from that PRD), else it's a hanging button. */}
                {thread.length > 0 && thread[thread.length - 1].reply && !busy && contentPanelTab === "prd" ? (
                  <div className="bc-suggest">
                    <div className="bc-suggest-list">
                      <button
                        type="button"
                        className="bc-suggest-btn bc-suggest-btn--primary"
                        disabled={!!activeTab?.prdGenerating || !activeTab?.prd}
                        onClick={() => {
                          if (activeTab?.prd) {
                            setContent({ prd: activeTab.prd, prdMeta: activeTab.briefMeta })
                            openContentPanel("tickets")
                          } else {
                            handleOpenPrd()
                          }
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d="M3 9a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1a2 2 0 0 0 0 4v1a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-1a2 2 0 0 0 0-4z" /><path d="M13 7v10" /></svg>
                        {activeTab?.prd ? "Create ticket" : "Generate PRD first"}
                      </button>
                    </div>
                  </div>
                ) : null}
                {/* Slash command dropdown */}
                {showSlash && filteredSkills.length > 0 && (
                  <div style={{
                    position: "absolute", bottom: "100%", left: 8, right: 8,
                    background: "var(--surface, #fff)", borderRadius: 10,
                    border: "1px solid var(--line, #E8E6E0)",
                    boxShadow: "0 -4px 20px rgba(0,0,0,0.08)", zIndex: 10,
                    maxHeight: 280, overflowY: "auto", padding: "6px 0",
                  }}>
                    <div style={{ padding: "4px 12px 6px", fontSize: 10.5, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--ink-4, #B0AEA6)" }}>
                      Skills
                    </div>
                    {filteredSkills.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        onClick={() => handleSlashSelect(s)}
                        style={{
                          display: "flex", alignItems: "flex-start", gap: 10, width: "100%",
                          padding: "8px 12px", background: "none", border: "none",
                          cursor: "pointer", textAlign: "left", fontSize: 13,
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--surface-2, #F4F1EA)" }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "none" }}
                      >
                        <span style={{
                          fontSize: 11, fontWeight: 600, color: "var(--accent, #179463)",
                          fontFamily: "var(--font-mono, monospace)", minWidth: 80, flexShrink: 0,
                        }}>
                          {s.trigger}
                        </span>
                        <span>
                          <span style={{ fontWeight: 500, color: "var(--ink, #1A1A17)" }}>{s.label}</span>
                          <span style={{ display: "block", fontSize: 11.5, color: "var(--ink-3, #8C8A84)", marginTop: 1 }}>{s.description}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                )}
                <div className="bc-composer">
                  <textarea
                    ref={composerRef}
                    className="bc-composer-input"
                    placeholder="Ask Sprntly anything, or type / for skills…"
                    rows={1}
                    value={draft}
                    onChange={handleComposerInput}
                    onKeyDown={handleComposerKeyDown}
                  />
                  <div className="bc-composer-bar">
                    <div className="bc-composer-tools">
                      <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.doc,.docx" style={{ display: "none" }} onChange={handleFileSelect} />
                      <button type="button" className="bc-tool" onClick={() => fileInputRef.current?.click()}>
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />
                        </svg>
                        Attach
                      </button>
                      <span className="bc-tool-kbd">
                        <kbd>⌘</kbd>
                        <kbd>/</kbd>
                      </span>
                    </div>
                    <button
                      type="button"
                      className="bc-send"
                      aria-label="Send"
                      disabled={busy || draft.trim().length < 1}
                      onClick={handleComposerSubmit}
                    >
                      <IconSendUp size={18} />
                    </button>
                  </div>
                </div>
                {/* Attached files preview */}
                {attachments.length > 0 && (
                  <div style={{ display: "flex", gap: 6, padding: "4px 24px 0", flexWrap: "wrap" }}>
                    {attachments.map((a, i) => (
                      <span key={i} style={{
                        display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11,
                        padding: "2px 8px", borderRadius: 5, background: "var(--surface-2, #F4F1EA)",
                        color: "var(--ink-2, #5A5853)", border: "1px solid var(--line, #E8E6E0)",
                      }}>
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        {a.name}
                        <button type="button" onClick={() => setAttachments((p) => p.filter((_, idx) => idx !== i))}
                          style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13, color: "var(--ink-4)", padding: 0, lineHeight: 1 }}>×</button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ) : null}
          </main>
          )}
        </div>
      </div>
      {chatGenModalOpen && chatGenPrdId != null && (
        <GenerateModal
          open={chatGenModalOpen}
          onClose={() => { if (!chatGenLoadingRef.current) setChatGenModalOpen(false) }}
          prdId={chatGenPrdId}
          figmaFileKey={chatGenFigmaKey}
          savedPreference={workspace?.design_source ?? null}
          onGenStart={handleChatGenStart}
          onKickoff={(id) => setChatGenProtoId(id)}
          onGenDone={handleChatGenDone}
        />
      )}
      <GenerationLoadingScreen
        open={chatGenLoading}
        figmaFileKey={chatGenFigmaKey}
        githubRepo={chatGenGithubRepo}
        prototypeId={chatGenProtoId}
      />
    </AppLayout>
  )
}
