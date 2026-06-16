"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
import type { ChatHomeCard } from "../../../types/content"
import { AppLayout } from "./AppLayout"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import { AskReplyBody } from "../../shared/AskReplyBody"
import { ChatSuggestionIcon, IconSendUp, IconSparkle } from "../../shared/app-icons"
import { ApiError, askApi, type AskResponse, type SkillInfo } from "../../../lib/api"
import { createChatPersistence, replyToText } from "../../../lib/chatPersistence"
import { isComposerBusy, runTabAsk } from "../../../lib/chatAskState"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import { runEvidenceGeneration } from "../../../lib/runEvidenceGeneration"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import type { PrdState, PrdContent } from "../../../types/content"
import { useBriefPrototypeMap } from "../../design-agent/useBriefPrototypeMap"
import { prototypePath } from "../../../lib/routes"
import { useRouter } from "next/navigation"
import { prototypeStateForInsight } from "../../design-agent/briefPrototypeMap.helpers"
import { GenerateModal } from "../../design-agent/GenerateModal"
import { GenerationLoadingScreen } from "../../design-agent/GenerationLoadingScreen"
import type { DesignAgentGenResult } from "../../../lib/runDesignAgentGeneration"

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
  /** Per-tab cached PRD (not persisted to localStorage — re-generate on reload). */
  prd: PrdState | null
  /** Per-tab cached evidence. */
  evidence: PrdContent | null
  prdGenerating: boolean
  evidenceGenerating: boolean
}

type HomeChipItem = { kind: "home" | "starter"; card: ChatHomeCard }

function buildHomeChips(home: ChatHomeCard[], starterList: ChatHomeCard[]): HomeChipItem[] {
  const out: HomeChipItem[] = []
  for (const card of home) {
    if (out.length >= 4) break
    out.push({ kind: "home", card })
  }
  for (const card of starterList) {
    if (out.length >= 4) break
    out.push({ kind: "starter", card })
  }
  return out
}

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
    showToast,
    openContentPanel,
    contentPanelTab,
  } = useNavigation()
  const router = useRouter()
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
      return localStorage.getItem(activeTabKey) || null
    } catch { return null }
  })

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
          prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
        })))
      } else {
        setTabs([])
      }
      setActiveTabId(localStorage.getItem(activeTabKey) || null)
    } catch {
      setTabs([])
      setActiveTabId(null)
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

  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null
  const thread = activeTab?.thread ?? []

  // ── Prototype map for the active tab's brief (one fetch per briefId) ───────
  const chatBriefId = activeTab?.briefMeta?.briefId ?? null
  const { entriesByInsight: chatEntriesByInsight } = useBriefPrototypeMap(chatBriefId)

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
  const [recording, setRecording] = useState(false)
  const [attachments, setAttachments] = useState<{ name: string; content: string }[]>([])
  // Per-tab in-flight guard — keyed by tabId. Prevents a tab from firing a second
  // ask while its own is still in flight, while letting OTHER tabs send concurrently.
  const askingTabsRef = useRef<Set<string>>(new Set())
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const recognitionRef = useRef<any>(null)

  // Voice: Web Speech API
  const toggleVoice = useCallback(() => {
    if (recording) {
      recognitionRef.current?.stop()
      setRecording(false)
      return
    }
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) { showToast("Not supported", "Voice input isn't supported in this browser."); return }
    const recognition = new SR()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = "en-US"
    recognition.onresult = (e: any) => {
      const transcript = Array.from(e.results as SpeechRecognitionResultList)
        .map((r: any) => r[0].transcript).join("")
      setDraft((prev) => prev + transcript)
    }
    recognition.onerror = () => setRecording(false)
    recognition.onend = () => setRecording(false)
    recognitionRef.current = recognition
    recognition.start()
    setRecording(true)
  }, [recording, showToast])

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
      briefMeta: briefMeta ?? null, prd: null, evidence: null,
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

  // ── Per-tab artifact generation ──────────────────────────────────────────
  const handleOpenPrd = useCallback(async () => {
    if (!activeTabId) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab || tab.prdGenerating) return
    // Already generated — sync to context and open panel
    if (tab.prd) {
      setContent({ prd: tab.prd, prdMeta: tab.briefMeta })
      openContentPanel("prd")
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
  }, [activeTabId, content.briefDetails, content.detail?.meta, openContentPanel, setContent, showToast])

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

  const submitAsk = useCallback(
    async (rawQuery: string) => {
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
      if (!activeTabId) {
        const title = query.length > 40 ? `${query.slice(0, 37)}…` : query
        targetTabId = openTab(title, [{ id, query }])
      } else {
        targetTabId = activeTabId
        setTabs((prev) => prev.map((t) =>
          t.id !== targetTabId ? t : { ...t, thread: [...t.thread, { id, query }] }
        ))
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
        ask: () => askApi.ask(query, activeCompany),
        onResult: (tabId, res) => {
          setTabs((prev) => prev.map((t) =>
            t.id !== tabId ? t : {
              ...t, thread: t.thread.map((turn) => turn.id === id ? { ...turn, reply: res } : turn)
            }
          ))
          finalizeConversationTurn(id, { reply: res }, tabId)
        },
        onError: (tabId, e) => {
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
    [activeCompany, activeTabId, attachments, finalizeConversationTurn, openTab, pushPendingConversation, showToast],
  )

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

  const startNewThread = () => {
    // Remove any empty tabs (no messages) to keep things clean
    setTabs((prev) => prev.filter((t) => t.thread.length > 0))
    setActiveTabId(null)
    setDraft("")
    setActiveConv(null)
    // No shared conv-id to reset — each tab tracks its own dbConvId.
  }

  const hasThread = thread.length > 0
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
          <div style={{
            display: "flex", alignItems: "stretch", gap: 0,
            borderBottom: "1px solid var(--line, #E8E6E0)", background: "var(--surface, #fff)",
            height: 40, overflowX: "auto", overflowY: "visible", flexShrink: 0,
          }}>
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
            <button
              type="button"
              onClick={startNewThread}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                background: "none", border: "none", cursor: "pointer",
                width: 32, fontSize: 18, color: "var(--ink-4, #B0AEA6)",
                flexShrink: 0, marginBottom: -1,
              }}
              title="New chat"
            >+</button>
          </div>

          <main className={`od-center ${hasThread ? "od-center--thread" : "od-center--landing"}`}>
            <div className={`od-center-scroll${!hasThread ? " od-center-scroll--home-landing" : ""}`}>
              {!hasThread ? (
                <div className="home-landing-eyeline">
                  <div className="od-center-inner od-center-inner--home">
                    <div className="chat-greeting">
                      <h1 className="chat-greeting-title">
                        Welcome back, <em>{name}</em>.
                      </h1>
                      <p className="chat-greeting-sub">Let&apos;s build something awesome.</p>
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
                            <button type="button" className="chat-home-action-btn" aria-label="Voice input">
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                                <line x1="12" y1="19" x2="12" y2="23"/>
                                <line x1="8" y1="23" x2="16" y2="23"/>
                              </svg>
                              Voice
                            </button>
                            <button type="button" className="chat-home-action-btn" aria-label="Attach file">
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
                            <span className="bc-agent-name">PM Agent</span>
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
                                disabled={!!activeTab?.prdGenerating}
                                onClick={handleOpenPrd}
                              >
                                {activeTab?.prdGenerating
                                  ? "Generating PRD…"
                                  : activeTab?.prd ? "Open PRD" : "Generate PRD"}
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
                                {chatInsightState?.hasPrd && chatInsightState.prototypeReady
                                  ? "View prototype"
                                  : "Generate prototype"}
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

            {hasThread ? (
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
                      <button
                        type="button"
                        className="bc-tool"
                        onClick={toggleVoice}
                        style={recording ? { color: "#DC2626" } : undefined}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                          <rect x="9" y="3" width="6" height="11" rx="3" />
                          <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                        </svg>
                        {recording ? "Stop" : "Voice"}
                      </button>
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
