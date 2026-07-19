"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useNavigation } from "../../../context/NavigationContext"
import { useContent } from "../../../context/ContentContext"
import { useCompany } from "../../../context/CompanyContext"
import { profileDisplayName, useWorkspace } from "../../../context/WorkspaceContext"
import { useAuth } from "../../../lib/auth"
import type { ChatHomeCard, ConversationRow } from "../../../types/content"
import { buildHomeChips, type HomeChipItem } from "../../../lib/homeChips"
import { AppLayout } from "./AppLayout"
import { BriefChat, isPrdCommand, isTicketsCommand } from "../../shared/BriefChat"
import { EmptyPane } from "../../shared/EmptyPane"
import { AssistantThinkingSkeleton } from "../../shared/AssistantThinkingSkeleton"
import { AskReplyBody } from "../../shared/AskReplyBody"
import { PrdInputQuestions } from "../../shared/PrdInputQuestions"
import { ChatSuggestionIcon, IconSendUp, IconSparkle, IconStop } from "../../shared/app-icons"
import { ApiError, askApi, briefApi, type AskResponse, type SkillInfo } from "../../../lib/api"
import { createChatPersistence, replyToText } from "../../../lib/chatPersistence"
import { addToSet, isComposerBusy, removeFromSet, runTabAsk } from "../../../lib/chatAskState"
import { runPrdGeneration, resumePrdGeneration, runPrdGenerationFromBacklog, loadPrdById } from "../../../lib/runPrdGeneration"
// resumePrdGeneration re-enters polling for an already-kicked-off PRD (the import path).
import type { PrdTabRequest } from "../../../context/NavigationContext"
import { runEvidenceGeneration, resumeEvidenceGeneration, loadEvidenceByInsight } from "../../../lib/runEvidenceGeneration"
import { runAskGeneration, resumeAskGeneration, getPendingAsk, AskCancelledError, AskStoppedError } from "../../../lib/runAskGeneration"
import { getPendingJob, insightScope } from "../../../lib/jobResume"
import { pickDefaultDetailKey } from "../../../lib/brief-adapter"
import type { PrdState, PrdContent } from "../../../types/content"
import { useBriefPrototypeMap } from "../../design-agent/useBriefPrototypeMap"
import { GeneratePrototypeCTA } from "../../design-agent/GeneratePrototypeCTA"
import { prototypePath } from "../../../lib/routes"
import { useRouter, useSearchParams } from "next/navigation"
import { prototypeStateForInsight } from "../../design-agent/briefPrototypeMap.helpers"
import { AGENT_NAME } from "../../../lib/agent"

type ThreadTurn = {
  id: string
  /** DISPLAY text — the user's typed ask only. Attached-document content is NOT
   *  folded in here (that goes to the backend separately); the thread renders
   *  this plus a chip per `attachments` entry, the way Claude's chat does. */
  query: string
  /** Files attached to this turn, shown as clickable cards above the ask. Each
   *  carries the extracted/plain-text `content` so the card can open a viewer —
   *  this is the SAME text folded into the backend query, never re-fetched. */
  attachments?: { name: string; content?: string }[]
  reply?: AskResponse
  error?: string
  /** The user stopped this ask before it answered (composer Stop button). Renders
   *  a muted "stopped" note instead of the thinking skeleton or an error bubble. */
  stopped?: boolean
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
  /** Per-tab cached PRD (not persisted to localStorage — reload restores it from
   *  the DB by `prdId`). */
  prd: PrdState | null
  /** This tab's OWN saved PRD id. Unlike the full `prd`, this small number IS
   *  persisted, so a reload can DB-load the exact PRD this tab is about — no
   *  regeneration, no reliance on the (mutable) brief insight→PRD map. It's the
   *  only recovery path for backlog PRDs, whose tabs carry no `briefMeta`. */
  prdId: number | null
  /** Per-tab cached evidence. */
  evidence: PrdContent | null
  prdGenerating: boolean
  evidenceGenerating: boolean
  /** True while a resumed conversation's turns are being fetched in the
   *  background (row click in All chats navigates instantly; the tab shows a
   *  loading state until the history lands). Transient — never persisted. */
  hydrating?: boolean
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

// The agent's acknowledgment for a command-opened PRD tab (seedQuery set on the
// request). Shown as the reply to the user's seeded command turn, so the chat
// explains what the spinning panel on the right is doing and how to get back to
// it (the PRD card above the thread hosts the View PRD button).
function commandAckReply(req: PrdTabRequest): AskResponse {
  const source = req.source
  const importing = source.kind === "resume"
  const withTickets = source.kind === "resume" && !!source.openTickets
  const answer = withTickets
    ? "Importing your document as a PRD — it'll open in the panel on the right, and I'll break it into tickets as soon as it's ready. Use the View PRD button above to reopen the panel anytime."
    : importing
      ? "Importing your document as a PRD — it'll open in the panel on the right when ready. Use the View PRD button above to reopen the panel anytime."
      : "Generating a PRD from this week's top insight — it'll open in the panel on the right when ready. Use the View PRD button above to reopen the panel anytime."
  return { answer, key_points: [], citations: [], confidence: 1, unanswered: "" }
}

// Attached-file chips shown under a composer. Rendered by BOTH the landing and
// thread composers — attachments live in shared state, so a file attached on
// the landing screen must be visible right there (the toast alone disappears in
// seconds, which read as "the upload didn't work"), not only after first send.
function AttachmentChips({ attachments, onRemove }: {
  attachments: { name: string }[]
  onRemove: (index: number) => void
}) {
  if (attachments.length === 0) return null
  return (
    <div style={{ display: "flex", gap: 6, padding: "4px 24px 0", flexWrap: "wrap" }}>
      {attachments.map((a, i) => (
        <span key={i} data-testid="attachment-chip" style={{
          display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11,
          padding: "2px 8px", borderRadius: 5, background: "var(--surface-2, #F4F1EA)",
          color: "var(--ink-2, #5A5853)", border: "1px solid var(--line, #E8E6E0)",
        }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          {a.name}
          <button type="button" aria-label={`Remove ${a.name}`} onClick={() => onRemove(i)}
            style={{ background: "none", border: "none", cursor: "pointer", fontSize: 13, color: "var(--ink-4)", padding: 0, lineHeight: 1 }}>×</button>
        </span>
      ))}
    </div>
  )
}

/** File extension, upper-cased (e.g. "DOCX"). Empty string when there's none. */
function fileTypeLabel(name: string): string {
  const dot = name.lastIndexOf(".")
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toUpperCase() : ""
}

/** Human "12 lines" / "3.4 KB" hint from the extracted text, for the card
 *  subtitle (mirrors how Claude shows a size/dimension line under a file). */
function attachmentMeta(name: string, content?: string): string {
  const type = fileTypeLabel(name)
  if (!content) return type || "File"
  const lines = content.split("\n").length
  return [type, `${lines.toLocaleString()} line${lines === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
}

/** A clickable file card on a user turn — Claude-style: an icon tile, the file
 *  name, and a type/size sub-line. Clicking opens the content viewer. */
function TurnAttachmentCard({
  name,
  content,
  onOpen,
}: {
  name: string
  content?: string
  onOpen: () => void
}) {
  const viewable = !!content
  return (
    <button
      type="button"
      className="bc-file-card"
      data-testid="turn-attachment-chip"
      onClick={viewable ? onOpen : undefined}
      disabled={!viewable}
      title={viewable ? `View ${name}` : name}
      aria-label={viewable ? `View ${name}` : name}
    >
      <span className="bc-file-card-icon" aria-hidden>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
        </svg>
      </span>
      <span className="bc-file-card-text">
        <span className="bc-file-card-name">{name}</span>
        <span className="bc-file-card-meta">{attachmentMeta(name, content)}</span>
      </span>
    </button>
  )
}

/** Full-screen overlay that renders an attachment's extracted content (the same
 *  text sent to the agent). Opened by clicking a file card on a user turn. */
function AttachmentViewer({
  attachment,
  onClose,
}: {
  attachment: { name: string; content: string }
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose()
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div className="bc-file-viewer-backdrop" role="dialog" aria-modal="true" aria-label={attachment.name} onClick={onClose}>
      <div className="bc-file-viewer" onClick={(e) => e.stopPropagation()}>
        <div className="bc-file-viewer-head">
          <span className="bc-file-viewer-title" title={attachment.name}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            {attachment.name}
          </span>
          <button type="button" className="bc-file-viewer-close" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="bc-file-viewer-body">
          {attachment.content.trim() ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{attachment.content}</ReactMarkdown>
          ) : (
            <p className="bc-file-viewer-empty">No preview available for this file.</p>
          )}
        </div>
      </div>
    </div>
  )
}

// Claude-style slash-command palette shown above the composer when the draft
// starts with "/". Rendered by BOTH composers (landing + thread) — the `inset`
// prop is the only positional difference (the dock composer is inset 8px).
// Keyboard-driven: the parent owns `activeIndex` (↑/↓/Enter) and the active row
// scrolls itself into view.
function SlashSkillMenu({ skills, activeIndex, onSelect, onHover, inset = false }: {
  skills: SkillInfo[]
  activeIndex: number
  onSelect: (skill: SkillInfo) => void
  onHover: (index: number) => void
  inset?: boolean
}) {
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(".chat-slash-item.is-active")
    active?.scrollIntoView({ block: "nearest" })
  }, [activeIndex])
  if (skills.length === 0) return null
  return (
    <div
      ref={listRef}
      className={`chat-slash-menu${inset ? " chat-slash-menu--inset" : ""}`}
      role="listbox"
      aria-label="Skills"
    >
      <div className="chat-slash-head">
        <span>Skills</span>
        <span className="chat-slash-count">{skills.length}</span>
      </div>
      {skills.map((s, i) => (
        <button
          key={s.id}
          type="button"
          role="option"
          aria-selected={i === activeIndex}
          className={`chat-slash-item${i === activeIndex ? " is-active" : ""}`}
          // Select on mousedown (before the textarea blurs) so the click always
          // lands even as focus moves.
          onMouseDown={(e) => { e.preventDefault(); onSelect(s) }}
          onMouseEnter={() => onHover(i)}
        >
          <span className="chat-slash-trigger">{s.trigger}</span>
          <span className="chat-slash-text">
            <span className="chat-slash-label">{s.label}</span>
            <span className="chat-slash-desc">{s.description}</span>
          </span>
          <span className="chat-slash-enter" aria-hidden>↵</span>
        </button>
      ))}
    </div>
  )
}

const DEFAULT_HOME_CHIPS: HomeChipItem[] = [
  { kind: "home", card: { id: "def-brief", icon: "sparkle", title: "View weekly brief", desc: "", target: "brief" } },
  { kind: "starter", card: { id: "def-analyze", icon: "chart", title: "Analyze data", desc: "", target: "ondemand", prompt: "Analyze our key product metrics and identify the top opportunities." } },
  { kind: "starter", card: { id: "def-draft", icon: "document", title: "Draft quarterly report", desc: "", target: "ondemand", prompt: "Draft a quarterly product report with key metrics, wins, and next steps." } },
  { kind: "starter", card: { id: "def-proto", icon: "rocket", title: "Prototype", desc: "", target: "ondemand", prompt: "Help me prototype the top feature in our product roadmap." } },
]

// The chat surface's artifact action row — EXACTLY two buttons. The first opens
// the first available artifact (View Evidence when the insight has evidence, else
// Generate/View PRD); the second is the Generate/View Prototype trigger, disabled
// until a PRD exists (a prototype is always built FROM a PRD). Shared by the
// insight-card row and the reply-footer row so the two never drift.
//
// The prototype button follows BriefChat's pattern: the shared GeneratePrototypeCTA
// with `skipExistenceCheck` (the batch prototype map — chatInsightState — is the
// existence source of truth, so no redundant per-tab getByPrd), driving Generate
// (open the modal) vs View (navigate) from `prototypeReady`.
function ChatArtifactActions({
  evidenceExists,
  prdExists,
  prdWaiting,
  prdGenerating,
  onViewEvidence,
  onOpenPrd,
  prototypePrdId,
  prototypeReady,
  onViewPrototype,
}: {
  evidenceExists: boolean
  prdExists: boolean
  prdWaiting: boolean
  prdGenerating: boolean
  onViewEvidence: () => void
  onOpenPrd: () => void
  prototypePrdId: number | null
  prototypeReady: boolean
  onViewPrototype: () => void
}) {
  const first = evidenceExists
    ? { label: "View Evidence", onClick: onViewEvidence, disabled: false }
    : {
        label: prdGenerating
          ? "Generating PRD…"
          : prdWaiting ? "Loading…"
          : prdExists ? "View PRD" : "Generate PRD",
        onClick: onOpenPrd,
        disabled: prdGenerating || prdWaiting,
      }
  const canPrototype = prototypePrdId != null
  return (
    <div className="bc-actions">
      <button
        type="button"
        className="bc-action-btn bc-action-btn--primary"
        disabled={first.disabled}
        onClick={first.onClick}
      >
        {first.label}
      </button>
      <GeneratePrototypeCTA
        prdId={prototypePrdId}
        skipExistenceCheck
        render={({ onClick }) => (
          <button
            type="button"
            className="bc-action-btn"
            data-testid="chat-prototype-cta"
            disabled={!canPrototype}
            title={canPrototype ? undefined : "Generate a PRD first"}
            onClick={canPrototype && prototypeReady ? onViewPrototype : onClick}
          >
            {canPrototype && prototypeReady ? "View Prototype" : "Generate Prototype"}
          </button>
        )}
      />
    </div>
  )
}

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
  const { profile } = useWorkspace()
  const { content, setContent } = useContent()
  const { activeCompany } = useCompany()
  const [railExpanded, setRailExpanded] = useState(false)
  const [activeConv, setActiveConv] = useState<number | null>(null)
  // Per-tab chat state is SESSION-scoped: it lives in sessionStorage, not
  // localStorage. So a fresh open (new browser tab/window, or reopening the app
  // after closing it) starts with ONLY the pinned Weekly-brief tab — never last
  // session's accumulated chat tabs. It still survives an in-session reload or a
  // navigate-away-and-back, so clicking around the app never nukes open chats.
  // Keys are ALSO user+company scoped so neither a different tenant nor a
  // different teammate signing in on this browser can see these tabs; sign-out
  // clears them outright (both storages) as defense in depth.
  const authUserId = auth.kind === "authed" ? auth.user.id : "anon"
  const tabsKey = `sprntly_chat_tabs_${authUserId}_${activeCompany}`
  const activeTabKey = `sprntly_chat_active_tab_${authUserId}_${activeCompany}`

  const [tabs, setTabs] = useState<ChatTab[]>(() => {
    try {
      const saved = sessionStorage.getItem(tabsKey)
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
        prdId: t.prdId ?? null,
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
      const stored = sessionStorage.getItem(activeTabKey)
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

  // When the storage key changes (workspace switch OR a different user signs
  // in), reload tabs from the new user+company-scoped session storage so we
  // never show another tenant's — or another teammate's — chat threads.
  const prevTabsKeyRef = useRef(tabsKey)
  useEffect(() => {
    if (prevTabsKeyRef.current === tabsKey) return
    prevTabsKeyRef.current = tabsKey
    try {
      const saved = sessionStorage.getItem(tabsKey)
      if (saved) {
        setTabs((JSON.parse(saved) as Partial<ChatTab>[]).map((t) => ({
          id: t.id ?? "", title: t.title ?? "", thread: t.thread ?? [],
          dbConvId: t.dbConvId ?? null, briefMeta: t.briefMeta ?? null,
          insightBody: t.insightBody ?? null, prdId: t.prdId ?? null,
          prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
        })))
      } else {
        setTabs([])
      }
      const storedActive = sessionStorage.getItem(activeTabKey)
      // No persisted active tab for this company → default to the pinned brief
      // tab; a persisted "" honours the chat landing (active tab = null).
      setActiveTabId(storedActive == null ? BRIEF_TAB_ID : storedActive || null)
    } catch {
      setTabs([])
      setActiveTabId(BRIEF_TAB_ID)
    }
  }, [activeCompany, tabsKey, activeTabKey])

  // Persist tabs to sessionStorage (session-scoped; see the key comment above) —
  // strip large/transient fields (prd, evidence, *Generating)
  useEffect(() => {
    try {
      const slim = tabs.map(({ prd: _p, evidence: _e, prdGenerating: _pg, evidenceGenerating: _eg, hydrating: _h, ...rest }) => rest)
      sessionStorage.setItem(tabsKey, JSON.stringify(slim))
    } catch { /* ignore */ }
  }, [tabs, tabsKey])
  useEffect(() => {
    try { sessionStorage.setItem(activeTabKey, activeTabId ?? "") } catch { /* ignore */ }
  }, [activeTabId, activeTabKey])

  // The pinned brief tab is synthesized (not in `tabs`), so when it's active
  // `activeTab` is null. `isBriefTab` lets the render swap in <BriefChat/> for
  // the chat landing/thread + composer.
  const isBriefTab = activeTabId === BRIEF_TAB_ID
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null
  const thread = activeTab?.thread ?? []

  // ── Prototype map for the active tab's brief (one fetch per briefId) ───────
  const chatBriefId = activeTab?.briefMeta?.briefId ?? null
  const { entriesByInsight: chatEntriesByInsight, loading: chatMapLoading } =
    useBriefPrototypeMap(chatBriefId)

  const chatInsightState = useMemo(() => {
    if (!activeTab?.briefMeta) return null
    return prototypeStateForInsight(chatEntriesByInsight, activeTab.briefMeta.insightIndex)
  }, [activeTab?.briefMeta, chatEntriesByInsight])

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
  // Insight keys ("briefId:insightIndex") known to already have a saved evidence
  // brief — flips the chat's first action to "View Evidence" (else it offers the
  // PRD). Populated per active insight via loadEvidenceByInsight (see effect below).
  const [insightsWithEvidence, setInsightsWithEvidence] = useState<ReadonlySet<string>>(new Set())
  const checkedEvidenceRef = useRef<Set<string>>(new Set())
  // Composer busy/disabled + "thinking" indicator reflect ONLY the active tab's
  // in-flight status. Another tab being mid-ask must not disable this composer.
  const busy = isComposerBusy(busyTabs, activeTabId)
  const [showSlash, setShowSlash] = useState(false)
  const [skills, setSkills] = useState<SkillInfo[]>([])
  const [slashFilter, setSlashFilter] = useState("")
  // Highlighted row in the slash palette (↑/↓ navigation, Enter selects).
  const [slashActive, setSlashActive] = useState(0)
  // `file` is set for document formats (.pdf/.pptx/.docx/.doc): those can't be
  // inlined as text client-side. The File feeds the PRD-import command
  // ("import this as a PRD" → POST /v1/prd/import) or, for a plain question,
  // server-side text extraction at send time (POST /v1/ask/extract-file).
  const [attachments, setAttachments] = useState<{ name: string; content: string; file?: File }[]>([])
  // The attachment whose content is open in the viewer overlay (click a file
  // card on a user turn). Null = closed.
  const [viewerAttachment, setViewerAttachment] = useState<{ name: string; content: string } | null>(null)
  // Per-tab in-flight guard — keyed by tabId. Prevents a tab from firing a second
  // ask while its own is still in flight, while letting OTHER tabs send concurrently.
  const askingTabsRef = useRef<Set<string>>(new Set())
  // Per-tab STOP flag — a tab id is present while the user has stopped its
  // in-flight ask. The ask poller reads this (isStopped) to bail; it's cleared
  // when a fresh ask starts on that tab so a stop never leaks into the next ask.
  const stoppedTabsRef = useRef<Set<string>>(new Set())
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // The scrolling thread viewport, so a new question (and the assistant's
  // thinking/answer under it) is scrolled into view instead of staying hidden
  // below the fold in a long conversation.
  const threadScrollRef = useRef<HTMLDivElement>(null)
  // Whether the user is pinned near the bottom. We only auto-follow streaming
  // replies while pinned, so scrolling up to read history isn't yanked back.
  const threadPinnedRef = useRef(true)
  const prevThreadLenRef = useRef(0)

  const scrollThreadToBottom = useCallback((behavior: ScrollBehavior) => {
    const el = threadScrollRef.current
    if (!el) return
    // Defer to the next frame so the just-added turn (and its thinking skeleton)
    // is laid out before we measure scrollHeight.
    requestAnimationFrame(() => {
      try {
        el.scrollTo({ top: el.scrollHeight, behavior })
      } catch {
        // jsdom / older engines without Element.scrollTo — set position directly.
        el.scrollTop = el.scrollHeight
      }
    })
  }, [])

  // Track whether the user is pinned near the bottom of the thread. Auto-follow
  // only applies while pinned, so scrolling up to read earlier turns during a
  // long answer isn't fought by the follow effect.
  const handleThreadScroll = useCallback(() => {
    const el = threadScrollRef.current
    if (!el) return
    threadPinnedRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120
  }, [])

  // Callback ref on the thread's content column. A ResizeObserver here keeps the
  // viewport pinned to the bottom as content GROWS — the thinking skeleton
  // appearing, then the answer typing in — not just on the initial render. That
  // covers the async growth a one-shot scroll misses. Re-attaches whenever the
  // content element mounts (tab switch, landing → thread), so it never observes
  // a stale node.
  const threadResizeObsRef = useRef<ResizeObserver | null>(null)
  const setThreadContentEl = useCallback((el: HTMLDivElement | null) => {
    threadResizeObsRef.current?.disconnect()
    threadResizeObsRef.current = null
    if (!el || typeof ResizeObserver === "undefined") return
    const ro = new ResizeObserver(() => {
      const scroller = threadScrollRef.current
      if (scroller && threadPinnedRef.current) scroller.scrollTop = scroller.scrollHeight
    })
    ro.observe(el)
    threadResizeObsRef.current = ro
  }, [])
  useEffect(() => () => threadResizeObsRef.current?.disconnect(), [])

  // A new turn (the user just asked) → re-pin and smooth-scroll so the question
  // + the assistant's thinking sit in view; the ResizeObserver then follows the
  // answer as it grows. Guard on a real length increase so a reply landing on an
  // existing turn doesn't double-trigger (the observer already handles growth).
  useEffect(() => {
    if (thread.length > prevThreadLenRef.current) {
      threadPinnedRef.current = true
      scrollThreadToBottom("smooth")
    }
    prevThreadLenRef.current = thread.length
  }, [thread.length, scrollThreadToBottom])

  // On tab switch/open, land at the bottom (newest turn) without animation and
  // reset the pinned state for the newly shown thread.
  useEffect(() => {
    prevThreadLenRef.current = thread.length
    threadPinnedRef.current = true
    scrollThreadToBottom("auto")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTabId, scrollThreadToBottom])

  // Attach: documents keep the real File (for the PRD-import command); plain-text
  // formats are read as text and inlined into the next ask as context.
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    Array.from(files).forEach((file) => {
      if (/\.(pdf|pptx|docx|doc)$/i.test(file.name)) {
        setAttachments((prev) => [...prev, { name: file.name, content: "", file }])
        return
      }
      const reader = new FileReader()
      reader.onload = () => {
        const content = reader.result as string
        setAttachments((prev) => [...prev, { name: file.name, content: content.slice(0, 50000) }])
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
        { id: "prd-author", label: "Generate PRD", trigger: "/prd", description: "Draft a product requirements document", category: "Documentation & Specification" },
        { id: "prioritize", label: "Prioritize", trigger: "/prioritize", description: "Rank ideas using RICE, ICE, MoSCoW, or WSJF", category: "Prioritization & Decision" },
        { id: "user-stories", label: "User stories", trigger: "/stories", description: "Break a PRD into user stories", category: "Documentation & Specification" },
        { id: "backlog-triage", label: "Triage backlog", trigger: "/triage", description: "Clean up backlog: cluster, dedupe", category: "Prioritization & Decision" },
        { id: "decision-memo", label: "Decision memo", trigger: "/decide", description: "Structure a build/buy decision", category: "Prioritization & Decision" },
        { id: "feedback-synthesis", label: "Feedback synthesis", trigger: "/feedback", description: "Synthesize feedback into themes", category: "Stakeholder & Communication" },
        { id: "competitive-intelligence-review", label: "Competitive analysis", trigger: "/compete", description: "Competitive intelligence review", category: "Strategy & Vision" },
        { id: "incident-runbook", label: "Incident runbook", trigger: "/incident", description: "Generate incident response runbook", category: "Delivery & Operations" },
        { id: "fact-check", label: "Fact-check", trigger: "/factcheck", description: "Verify claims against sources", category: "Verification" },
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
      briefMeta: briefMeta ?? null, insightBody: null, prd: null, prdId: null, evidence: null,
      prdGenerating: false, evidenceGenerating: false,
    }])
    setActiveTabId(id)
    setDraft("")
    return id
  }, [])

  const closeTab = useCallback((tabId: string) => {
    const next = tabsRef.current.filter((t) => t.id !== tabId)
    setTabs(next)
    // Closing the ACTIVE tab hands focus to the last surviving chat tab; when
    // none remain, the pinned Weekly-brief tab becomes active — never the
    // tab-less landing (which left NO tab looking active in the strip).
    if (activeTabIdRef.current === tabId) {
      setActiveTabId(next.length > 0 ? next[next.length - 1].id : BRIEF_TAB_ID)
    }
  }, [])

  // Rehydrate a PRD tab's chat thread from its saved conversation. A PRD's chat
  // is keyed by prd_id in Supabase (conversationsApi.byPrd), so reopening a PRD —
  // even on a new device or after the localStorage tab is gone — restores the
  // user's earlier questions + Sprntly's answers instead of an empty thread. Only
  // ever fills a still-empty, unconverted tab (guarded again inside the setter so
  // a race with live typing can't clobber it); non-fatal on any failure.
  const hydratePrdThread = useCallback(async (tabId: string, prdId: number) => {
    // A just-created tab may not be in tabsRef yet (state not flushed), so DON'T
    // bail when it's missing — only when it's present AND already has content. The
    // setTabs guard below re-checks, so a genuinely absent tab is a harmless no-op.
    const tab = tabsRef.current.find((t) => t.id === tabId)
    if (tab && (tab.thread.length > 0 || tab.dbConvId != null)) return
    try {
      const { conversationsApi } = await import("../../../lib/api")
      const { conversation, turns } = await conversationsApi.byPrd(prdId)
      if (!conversation || turns.length === 0) return
      const restored: ThreadTurn[] = []
      for (let i = 0; i < turns.length; i++) {
        const t = turns[i]
        if (t.role === "user") {
          const next = turns[i + 1]
          const reply = next?.role === "assistant"
            ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
            : undefined
          restored.push({ id: `prdhist-${conversation.id}-${i}`, query: t.content, reply })
          if (reply) i++
        }
      }
      if (restored.length === 0) return
      setTabs((prev) => prev.map((t) =>
        t.id === tabId && t.thread.length === 0 && t.dbConvId == null
          ? { ...t, thread: restored, dbConvId: conversation.id }
          : t))
    } catch { /* non-fatal: fall back to an empty thread */ }
  }, [])

  // The open-path prd_id (source ready/load, or a generate that resolves) is
  // unreliable: "View PRD" degrades to a generate/find-or-create when the
  // insight→PRD map hasn't populated yet, so the tab's prdId can stay null. But
  // chatInsightState resolves the ACTIVE tab's real PRD id from that same map,
  // keyed by briefMeta — and it lands reliably once the map loads, independent of
  // the open path. So whenever we know the active tab's prd_id: (1) backfill
  // tab.prdId (null only) so chat persistence stamps the right PRD, and (2)
  // rehydrate the saved chat (guarded to an empty, unconverted tab). This is what
  // makes a reopened PRD actually restore its prior questions.
  const resolvedInsightPrdId = chatInsightState?.hasPrd ? chatInsightState.prdId : null
  useEffect(() => {
    if (resolvedInsightPrdId == null || activeTabId == null) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    if (!tab) return
    if (tab.prdId == null) {
      setTabs((prev) => prev.map((t) =>
        t.id === activeTabId && t.prdId == null ? { ...t, prdId: resolvedInsightPrdId } : t))
    }
    if (tab.thread.length === 0 && tab.dbConvId == null) {
      void hydratePrdThread(activeTabId, resolvedInsightPrdId)
    }
  }, [activeTabId, resolvedInsightPrdId, hydratePrdThread])

  // ── Open a PRD as a NEW CHAT TAB with the content panel over it ─────────────
  // A "view/generate PRD" from another surface (brief cards, brief composer,
  // backlog) routes here via NavigationContext.openPrdTab → pendingPrdTab. We
  // spawn (or reuse, by title) a fresh chat tab, drive the requested source into
  // its cached PRD + the shared ContentContext, and flag the content panel to
  // slide open (deferred a commit so the route-change close can't swallow it).
  // The PRD/Evidence/Tickets all render in that panel — the tab itself is a
  // normal chat the user can keep talking in. Returns the target tab's id so
  // the consumer can persist a seeded command turn against it.
  const openPrdInTab = useCallback((req: PrdTabRequest): string => {
    const { title, source } = req
    const meta = source.kind === "generateBacklog" ? null : source.meta
    const existing = tabsRef.current.find((t) => t.title === title)
    const tabId = existing?.id ?? `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
    // A command phrasing opened this tab ("convert this PRD into tickets",
    // "generate a PRD"): seed the thread with the user's message + an
    // acknowledgment, so the chat shows WHY a generation is running instead of
    // sitting empty next to the spinning panel.
    const seedTurn: ThreadTurn | null = req.seedQuery
      ? { id: `seed-${Date.now()}`, query: req.seedQuery, reply: commandAckReply(req) }
      : null
    if (existing) {
      setActiveTabId(existing.id)
      // Backfill the insight body onto an already-open tab that lacks one (e.g. a
      // tab created before this field existed, or opened via a path that didn't
      // carry it) so reopening the insight surfaces its content, not just a title.
      // A re-issued command appends its turn to the existing thread.
      if ((req.insightBody && !existing.insightBody) || seedTurn) {
        setTabs((prev) => prev.map((t) => t.id === existing.id ? {
          ...t,
          insightBody: t.insightBody ?? req.insightBody ?? null,
          thread: seedTurn ? [...t.thread, seedTurn] : t.thread,
        } : t))
      }
    } else {
      setTabs((prev) => [...prev, {
        id: tabId, title, thread: seedTurn ? [seedTurn] : [], dbConvId: null, briefMeta: meta,
        insightBody: req.insightBody ?? null, prdId: null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
      }])
      setActiveTabId(tabId)
    }
    setDraft("")
    setPrdPanelPending(true)

    // Reopening an EXISTING PRD (ready | load)? Rehydrate its saved chat thread by
    // prd_id so the user's prior questions come back. New PRDs (generate*) have no
    // prior conversation, so we skip — their prd_id is stamped on first send.
    const knownPrdId = source.kind === "ready" ? source.prd.prd_id
      : source.kind === "load" ? source.prdId
      : source.kind === "resume" ? source.prdId
      : null
    if (knownPrdId != null) void hydratePrdThread(tabId, knownPrdId)

    // Reuse a PRD already cached on this tab (unless the caller handed us a fresh
    // one) — don't regenerate/re-fetch an already-open PRD.
    if (existing?.prd && source.kind !== "ready") {
      setContent({ prd: existing.prd, prdMeta: existing.briefMeta, prdGenerating: false })
      return tabId
    }
    // Caller already holds the PRD — show it immediately, no async work.
    if (source.kind === "ready") {
      setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: source.prd, prdId: source.prd.prd_id, briefMeta: source.meta } : t))
      setContent({ prd: source.prd, prdMeta: source.meta, prdGenerating: false })
      return tabId
    }
    // generate | generateBacklog | load | resume — kick off, show the panel's
    // spinner, then land the result on the tab (and shared content while active).
    setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: null, briefMeta: meta, prdGenerating: true } : t))
    setContent({ prd: null, prdMeta: meta, prdGenerating: true })
    void (async () => {
      try {
        const result =
          source.kind === "generate" ? await runPrdGeneration(source.meta)
          : source.kind === "generateBacklog" ? await runPrdGenerationFromBacklog(source.backlogItemId)
          : source.kind === "resume" ? await resumePrdGeneration(source.prdId, source.meta ?? undefined)
          : await loadPrdById(source.prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false } : t))
          if (activeTabIdRef.current === tabId) setContent({ prd: result.prd, prdMeta: meta, prdGenerating: false })
          // "convert this PRD into tickets": the user asked for TICKETS — once the
          // imported PRD is ready, switch the panel to the Tickets tab (which
          // kicks off user-stories generation for it). Only while this tab is
          // still active — never yank the panel out from under another tab.
          if (source.kind === "resume" && source.openTickets && activeTabIdRef.current === tabId) {
            openContentPanel("tickets")
          }
          // The prd_id was UNKNOWN upfront (generate | generateBacklog — including
          // "View PRD" find-or-create, which resolves an EXISTING PRD). Now that we
          // have it, rehydrate the tab's chat by prd_id. New PRDs return no
          // conversation (no-op); an existing one restores the user's prior turns.
          // The upfront ready/load path already hydrated, so skip those here.
          if (knownPrdId == null) void hydratePrdThread(tabId, result.prd.prd_id)
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
    return tabId
  }, [setContent, showToast, hydratePrdThread, openContentPanel])

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
    // A PRD already exists in the DB for this tab but isn't cached — e.g. after a
    // reload, where `prd` is stripped from the persisted tab. LOAD it by id; do
    // NOT regenerate (that would spawn a duplicate and burn a full generation).
    // This is what makes "View PRD" open the real doc rather than kick off a build.
    //
    // Prefer this tab's OWN saved id: it's stable across brief regeneration and is
    // the ONLY recovery path for backlog PRDs (whose tabs carry no briefMeta). Fall
    // back to the brief insight→PRD map for older tabs that predate `prdId`.
    const savedPrdId = tab.prdId ?? (chatInsightState?.hasPrd ? chatInsightState.prdId : null)
    if (savedPrdId != null) {
      const prdId = savedPrdId
      setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: true } : t))
      setContent({ prd: null, prdMeta: null, prdGenerating: true })
      openContentPanel("prd")
      try {
        const result = await loadPrdById(prdId)
        if (result.ok) {
          setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
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
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
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
    // Already generated — sync to context and open panel. Stamp the insight meta
    // so the Evidence tab's "Generate/View PRD" bar knows which insight to act on.
    if (tab.evidence) {
      setContent({ evidence: tab.evidence, ...(tab.briefMeta ? { prdMeta: tab.briefMeta } : {}) })
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
    setContent({ evidence: null, evidenceGenerating: true, prdMeta: meta })
    openContentPanel("evidence")
    try {
      const result = await runEvidenceGeneration(meta)
      if (result.ok) {
        setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, evidenceGenerating: false, evidence: result.evidence } : t))
        setContent({ evidence: result.evidence, evidenceGenerating: false, prdMeta: meta })
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
              setTabs((prev) => prev.map((t) => t.id === activeTabId ? { ...t, prdGenerating: false, prd: result.prd, prdId: result.prd.prd_id } : t))
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
      getTabPrdId: (tabId) => tabsRef.current.find((t) => t.id === tabId)?.prdId ?? null,
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

  // Resume a conversation from ChatsScreen or BacklogScreen. Two payload
  // shapes: with `turns` (built locally / legacy) the tab opens pre-filled;
  // with only a `dbId` (All-chats row click) the tab opens INSTANTLY in a
  // `hydrating` state and the turns are fetched here in the background — the
  // click never blocks on the network.
  const checkResume = useCallback(() => {
    try {
      const raw = localStorage.getItem("sprntly_resume_conv")
      if (!raw) return
      localStorage.removeItem("sprntly_resume_conv")
      const data = JSON.parse(raw) as {
        dbId: number
        title: string
        turns?: { role: string; content: string }[]
        /** Preview-derived thread used when the background fetch yields nothing. */
        fallbackTurns?: { role: string; content: string }[]
      }
      const buildRestored = (
        turns: { role: string; content: string }[],
        keyPrefix: string,
      ): ThreadTurn[] => {
        const restored: ThreadTurn[] = []
        for (let i = 0; i < turns.length; i++) {
          const t = turns[i]
          if (t.role === "user") {
            const next = turns[i + 1]
            const reply = next?.role === "assistant" ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse : undefined
            restored.push({ id: `${keyPrefix}-${i}`, query: t.content, reply })
            if (reply) i++
          }
        }
        return restored
      }

      // Pre-fetched turns → open filled (BacklogScreen + no-dbId fallback).
      const preloaded = buildRestored(data.turns ?? [], "resumed")
      if (preloaded.length > 0) {
        // The resumed tab's dbConvId is set via openTab(..., data.dbId) —
        // per-tab now, no shared ref.
        openTab(data.title || "Resumed chat", preloaded, data.dbId)
        setActiveConv(0)
        return
      }

      // dbId only → open the tab NOW, fetch its history in the background.
      if (!data.dbId) return
      const tabId = openTab(data.title || "Resumed chat", [], data.dbId)
      setActiveConv(0)
      // openTab reuses an existing same-title tab; if it already carries a
      // thread there's nothing to hydrate.
      const existing = tabsRef.current.find((t) => t.id === tabId)
      if (existing && existing.thread.length > 0) return
      setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, hydrating: true } : t)))
      const fallback = buildRestored(data.fallbackTurns ?? [], `resumed-fb-${data.dbId}`)
      void (async () => {
        const { conversationsApi } = await import("../../../lib/api")
        // Fetch the conversation's turns, RETRYING on a transient failure. This
        // is the whole point of the resume: a single failed request must never
        // silently collapse a multi-ask chat down to the preview-only fallback
        // (a lone opening question that looks like a brand-new chat — the exact
        // reported bug). `restored === null` means every attempt errored; an
        // empty array is a genuine empty conversation.
        let restored: ThreadTurn[] | null = null
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            const res = await conversationsApi.listTurns(data.dbId)
            restored = buildRestored(res.turns ?? [], `resumed-${data.dbId}`)
            break
          } catch {
            if (attempt < 2) await new Promise((r) => setTimeout(r, 250 * (attempt + 1)))
          }
        }
        // Prefer the fetched thread whenever it has turns; otherwise the
        // preview-derived fallback keeps the tab usable.
        const finalThread = restored && restored.length > 0 ? restored : fallback
        // Guarded fill: never clobber a thread the user started meanwhile, and
        // never REPLACE a fuller thread (e.g. a reused open tab) with a thinner
        // preview — only fill a still-empty tab.
        setTabs((prev) => prev.map((t) =>
          t.id === tabId
            ? { ...t, hydrating: false, thread: t.thread.length === 0 ? finalThread : t.thread }
            : t))
        // If every attempt failed, say so instead of silently showing a partial
        // thread — silence is what made a temporarily-unreachable history look
        // like the chat had lost its messages.
        if (restored === null) {
          showToast("Couldn't load full chat history", "Showing a preview — reopen the chat to retry.")
        }
      })()
    } catch { /* ignore corrupt data */ }
  }, [openTab, showToast])
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
      // ONE rail entry per chat tab (mirrors the one-conversation-per-tab DB
      // invariant in chatPersistence.ts). A follow-up message in the same room
      // UPDATES that room's entry (latest turn, bumped time, moved to top)
      // instead of prepending a new row — otherwise every message showed up as
      // its own item in the History list until the next page reload.
      const existing = prev.find((c) => (c as any)._tabId === targetTabId)
      if (existing) {
        setContent({
          conversations: [
            { ...existing, time: timeStr, savedTurn: { id: turnId, query } },
            ...prev.filter((c) => c !== existing),
          ],
        })
      } else {
        setContent({
          conversations: [
            {
              id: turnId,
              title,
              time: timeStr,
              savedTurn: { id: turnId, query },
              _tabId: targetTabId,
            } as ConversationRow,
            ...prev,
          ],
          sidebarConvCount: prev.length + 1,
        })
      }
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
          // Match on the entry's CURRENT saved turn: with one rail entry per
          // tab, later turns land on the same entry, whose id stays the first
          // turn's id. A stale finalize (the entry has since moved on to a
          // newer turn) is dropped rather than clobbering the newer query.
          if (c.savedTurn?.id !== turnId) return c
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
  const prdCommandFlow = useCallback(async (seedQuery?: string) => {
    try {
      const brief = await briefApi.current(activeCompany)
      const insights = brief.insights || []
      if (!insights.length) {
        showToast("No brief yet", "Run the pipeline to refresh this week's brief first.")
        return
      }
      openPrdTab({
        title: "PRD · Weekly brief",
        seedQuery,
        source: { kind: "generate", meta: { briefId: brief.id, insightIndex: 0 } },
      })
    } catch (e) {
      showToast("PRD generation failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    }
  }, [activeCompany, openPrdTab, showToast])

  // A command phrasing over an ATTACHED DOCUMENT is the chat entry to the
  // PRD-import flow: upload the doc to POST /v1/prd/import — the same conversion
  // the Artifacts "Upload PRD" button uses (parse to text, faithful re-layout
  // into the chat-PRD format) — then open the imported PRD as its own chat tab.
  // With `openTickets` ("convert this PRD into tickets") the panel lands on the
  // Tickets tab once the PRD is ready, which generates user stories for it.
  const importPrdCommandFlow = useCallback(async (file: File, opts: { openTickets: boolean; seedQuery?: string }) => {
    try {
      const { prdApi } = await import("../../../lib/api")
      const start = await prdApi.importDoc(file, activeCompany)
      openPrdTab({
        title: start.title || file.name,
        seedQuery: opts.seedQuery,
        source: { kind: "resume", prdId: start.prd_id, meta: null, openTickets: opts.openTickets },
      })
    } catch (e) {
      showToast("PRD import failed", (e instanceof Error ? e.message : String(e)).slice(0, 200))
    }
  }, [activeCompany, openPrdTab, showToast])

  const submitAsk = useCallback(
    async (rawQuery: string) => {
      const trimmed = rawQuery.trim()
      // Command phrasings are COMMANDS, not questions for the ask agent —
      // intercept before any tab/ask work. Tickets is checked FIRST: "create
      // tickets from this PRD" matches the PRD rule too, but the user asked for
      // tickets. With a document attached, either phrasing imports the doc as a
      // PRD; "…tickets" additionally lands on the Tickets tab when it's ready.
      const docFile = attachments.find((a) => a.file)?.file ?? null
      if (isTicketsCommand(trimmed)) {
        if (docFile) {
          setAttachments([])
          void importPrdCommandFlow(docFile, { openTickets: true, seedQuery: trimmed })
          return
        }
        // No document: mirror the reply-footer "Create tickets" action when this
        // tab already carries a PRD. Otherwise fall through to the ask agent
        // (the user-stories skill answers in markdown, as before).
        const tab = activeTabId ? tabsRef.current.find((t) => t.id === activeTabId) : undefined
        if (tab?.prd) {
          setContent({ prd: tab.prd, prdMeta: tab.briefMeta })
          openContentPanel("tickets")
          return
        }
      } else if (isPrdCommand(trimmed)) {
        if (docFile) {
          setAttachments([])
          void importPrdCommandFlow(docFile, { openTickets: false, seedQuery: trimmed })
          return
        }
        // No document — open the PRD tab from the brief's top insight instead of
        // sending it to the ask agent (which would answer with a raw prd-author
        // HTML dump).
        void prdCommandFlow(trimmed)
        return
      }
      // Append attached file content as context. Text attachments inline
      // directly; document attachments (.pdf/.pptx/.docx/.doc) are parsed to
      // markdown server-side (POST /v1/ask/extract-file) so a deck attached to
      // a plain question reaches the agent too — they used to be silently
      // dropped here, which read as "no document was attached" replies.
      // `displayQuery` is what the thread shows (the user's ask, plus a chip per
      // attachment — never the raw document dump). `sendQuery` is what the ask
      // agent receives: the same text with the parsed attachment content folded
      // in, exactly as before. Keeping them separate means the backend is
      // unaffected while the UI stays clean, the way Claude's chat renders it.
      const displayQuery = trimmed
      let turnAttachments: { name: string; content?: string }[] = []
      let sendQuery = trimmed
      if (attachments.length > 0) {
        let ctx: string
        try {
          // Extract each attachment's text ONCE: plain-text attachments inline
          // their content; documents (.pdf/.pptx/.docx/.doc) are parsed to
          // markdown server-side. Order is preserved via the resolved array so
          // the same text feeds BOTH the backend query and the clickable card's
          // viewer — the document is never re-fetched to display it.
          const extracted = await Promise.all(
            attachments.map(async (a) => {
              const text = a.file
                ? (await askApi.extractFile(a.file)).markdown.slice(0, 50000)
                : a.content
              return { name: a.name, content: text }
            }),
          )
          // Clamp the TOTAL context so question + attachments stay under the
          // ask endpoint's 120k question cap even with several attachments.
          ctx = extracted
            .map((e) => `--- ${e.name} ---\n${e.content}`)
            .join("\n\n")
            .slice(0, 100000)
          turnAttachments = extracted.map((e) => ({ name: e.name, content: e.content }))
        } catch (e) {
          // Keep the attachments so the user can retry or remove the bad one —
          // a silent drop is exactly the failure mode this path exists to fix.
          showToast("Couldn't read attachment", (e instanceof Error ? e.message : String(e)).slice(0, 200))
          return
        }
        sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
        setAttachments([]) // clear after successful extraction only
      }
      if (sendQuery.length < 1) return
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
      // The thread turn shows the ASK (displayQuery) + a chip per attachment,
      // never the folded-in document content that rides sendQuery to the backend.
      const newTurn: ThreadTurn = {
        id,
        query: displayQuery,
        ...(turnAttachments.length ? { attachments: turnAttachments } : {}),
      }
      // The tab title/handle falls back to the first attachment's name when the
      // ask itself is empty, so a doc-only send still reads sensibly in the tab.
      const handle = displayQuery || turnAttachments[0]?.name || "New chat"
      // No active tab, OR the active "tab" is the synthetic, thread-less brief
      // tab → spawn a FRESH chat tab seeded with the query. A chat started from
      // the weekly brief must never thread inline into it (the brief tab carries
      // no `tabs` entry, so appending would silently no-op anyway).
      if (!activeTabId || activeTabId === BRIEF_TAB_ID) {
        const title = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        targetTabId = openTab(title, [newTurn])
      } else {
        targetTabId = activeTabId
        const newTitle = handle.length > 40 ? `${handle.slice(0, 37)}…` : handle
        setTabs((prev) => prev.map((t) => {
          if (t.id !== targetTabId) return t
          // First message in a placeholder "New chat" tab → give it the real
          // title from the query (rename in place; do NOT spawn a second tab).
          const title = t.thread.length === 0 && t.title === NEW_CHAT_TITLE ? newTitle : t.title
          return { ...t, title, thread: [...t.thread, newTurn] }
        }))
      }
      pushPendingConversation(id, displayQuery, targetTabId)
      setActiveConv(0)
      // A fresh ask on this tab clears any leftover Stop flag from a prior ask so
      // the new one is never treated as pre-stopped.
      stoppedTabsRef.current.delete(targetTabId)
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
        ask: () => runAskGeneration(sendQuery, activeCompany, targetTabId, {
          isCancelled: () => !mountedRef.current,
          isStopped: () => stoppedTabsRef.current.has(targetTabId),
        }),
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
          // User hit Stop: the stopped turn is already rendered by handleStopAsk.
          // Not a failure — no error bubble/toast.
          if (e instanceof AskStoppedError) return
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
    [activeCompany, activeTabId, attachments, finalizeConversationTurn, importPrdCommandFlow, openContentPanel, openTab, prdCommandFlow, pushPendingConversation, setContent, showToast],
  )

  // ── Stop an in-flight ask ─────────────────────────────────────────────────
  // The composer's Send button becomes a Stop button while the active tab's ask
  // is generating. Stopping is deliberate (unlike a background unmount): it
  // reclaims the composer AT ONCE, marks the in-flight turn `stopped`, and asks
  // the backend to cancel so the worker aborts before its next LLM step and any
  // late answer is discarded server-side.
  const handleStopAsk = useCallback(() => {
    const tabId = activeTabId
    if (!tabId) return
    // 1) Signal the running poller to bail — it clears the persisted ask_id (so a
    //    remount won't resume) and rejects with AskStoppedError, which onError
    //    swallows. Checked on the poll's next tick.
    stoppedTabsRef.current.add(tabId)
    // 2) Best-effort backend cancel: the worker polls the job status between LLM
    //    steps and aborts before the expensive answer call when it lands early.
    const pending = getPendingAsk(activeCompany, tabId)
    if (pending) {
      const askId = Number(pending.id)
      if (Number.isFinite(askId)) void askApi.cancel(askId).catch(() => {})
    }
    // 3) Reclaim the composer immediately rather than waiting for the poll's next
    //    tick (runTabAsk's finally also clears these — the double-clear is safe).
    askingTabsRef.current.delete(tabId)
    setBusyTabs((prev) => removeFromSet(prev, tabId))
    // 4) Replace the in-flight turn's thinking skeleton with a muted stopped note.
    //    The in-flight turn is the last one still awaiting a reply.
    setTabs((prev) => prev.map((t) => {
      if (t.id !== tabId) return t
      let idx = -1
      for (let i = t.thread.length - 1; i >= 0; i--) {
        const turn = t.thread[i]
        if (!turn.reply && !turn.error && !turn.stopped) { idx = i; break }
      }
      if (idx === -1) return t
      return { ...t, thread: t.thread.map((turn, i) => i === idx ? { ...turn, stopped: true } : turn) }
    }))
  }, [activeTabId, activeCompany, setBusyTabs])

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
    const tabId = openPrdInTab(req)
    // A command-seeded turn (already rendered in the tab's thread by
    // openPrdInTab) also lands in the conversations rail + Supabase, so the
    // exchange survives a reload like any other chat turn.
    if (req.seedQuery) {
      const turnId = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
      pushPendingConversation(turnId, req.seedQuery, tabId)
      finalizeConversationTurn(turnId, { reply: commandAckReply(req) }, tabId)
    }
  }, [pendingPrdTab, setPendingPrdTab, openPrdInTab, pushPendingConversation, finalizeConversationTurn])

  // Slide the content panel open on the commit AFTER openPrdInTab flags it. The
  // deferral matters when the PRD was opened from another surface: openPrdTab
  // routes to `/`, and NavigationContext closes the panel on that route change —
  // opening it here a commit later (route now settled) survives that close.
  useEffect(() => {
    if (!prdPanelPending) return
    setPrdPanelPending(false)
    openContentPanel("prd")
  }, [prdPanelPending, openContentPanel])

  // The content panel is a single global overlay, but it must FOLLOW the active
  // tab: a PRD-bound tab shows its PRD on the right; the brief tab or a plain chat
  // shows nothing. Because the panel is global, this has to be reconciled on every
  // genuine tab switch. On switching to…
  //   • a PRD-bound tab (a PRD already cached/generating, or one in the DB) → open
  //     it (handleOpenPrd syncs the cached PRD or DB-loads by id). This is what
  //     makes REFOCUSING a PRD tab bring its panel back, instead of leaving it
  //     closed after you'd visited another tab.
  //   • the brief tab, or a plain (non-PRD) chat → close any lingering panel so it
  //     never hangs over the wrong surface.
  // Gated on an actual switch (prevTabForPanelRef) so a manual panel-close while
  // staying on a tab isn't immediately undone, and so the brief's own inline
  // actions (Tickets / Evidence / multi-agent — which open the panel WITHOUT a
  // switch) are untouched. `prdPanelPending` (set by openPrdInTab a commit before
  // it opens the panel) suppresses the reconcile during that hand-off.
  const autoRestoredTabsRef = useRef<Set<string>>(new Set())
  const prevTabForPanelRef = useRef(activeTabId)
  useEffect(() => {
    const switchedTab = prevTabForPanelRef.current !== activeTabId
    prevTabForPanelRef.current = activeTabId
    if (!switchedTab || prdPanelPending) return
    // Brief tab or the tab-less landing → no PRD to show; drop any lingering panel.
    if (isBriefTab || !activeTabId) { if (contentPanelTab) closeContentPanel(); return }
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    const ownsPrd = !!tab?.prd || !!tab?.prdGenerating || tab?.prdId != null
      || !!(chatInsightState?.hasPrd && chatInsightState.prdId != null)
    if (ownsPrd) {
      // Sync the global panel to THIS tab's PRD — ALWAYS, even if it already reads
      // "prd", because another PRD tab may have left ITS doc in the shared panel
      // (that was the "wrong PRD on refocus" bug). handleOpenPrd uses the cached
      // prd (instant) or DB-loads this tab's own id. Pre-claim the tab so the
      // reload-restore effect below doesn't ALSO fire handleOpenPrd this commit.
      autoRestoredTabsRef.current.add(activeTabId)
      void handleOpenPrd()
    } else if (contentPanelTab) {
      closeContentPanel()
    }
  }, [activeTabId, isBriefTab, contentPanelTab, prdPanelPending, chatInsightState, handleOpenPrd, closeContentPanel])

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
  //   • Fires at most once per tab (autoRestoredTabsRef, shared with the switch
  //     reconcile above so the two never double-open the same tab in one commit).
  useEffect(() => {
    if (!activeTabId || isBriefTab) return
    if (autoRestoredTabsRef.current.has(activeTabId)) return
    const tab = tabsRef.current.find((t) => t.id === activeTabId)
    // Already loaded/loading, or a panel is already open → nothing to restore right
    // now (don't latch; these conditions are transient).
    if (!tab || tab.prd || tab.prdGenerating || contentPanelTab) return
    // This tab's OWN saved id restores immediately — no map needed. This is the
    // path that brings back a backlog PRD (no briefMeta) after a reload.
    if (tab.prdId != null) {
      autoRestoredTabsRef.current.add(activeTabId)
      void handleOpenPrd()
      return
    }
    // Otherwise it must be a brief-insight tab whose DB PRD the map confirms. A
    // not-yet-resolved map reads as hasPrd=false → treat as "wait", not "give up",
    // and re-check on the next render (the empty pre-fetch window latch bug).
    if (!tab.briefMeta) return
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
      if (!last || last.reply !== undefined || last.error !== undefined || last.stopped) continue
      if (askingTabsRef.current.has(tab.id)) continue
      resumedAskTabsRef.current.add(tab.id)
      const turnId = last.id
      const targetTabId = tab.id
      // Restore the optimistic asking/busy UX for this tab.
      askingTabsRef.current.add(targetTabId)
      setBusyTabs((prev) => addToSet(prev, targetTabId))
      stoppedTabsRef.current.delete(targetTabId)
      void (async () => {
        try {
          const res = await resumeAskGeneration(
            askId,
            activeCompany,
            targetTabId,
            () => !mountedRef.current,
            () => stoppedTabsRef.current.has(targetTabId),
          )
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
          // User stopped the resumed ask: the stopped turn is rendered by
          // handleStopAsk; not a failure, so no error bubble.
          if (e instanceof AskStoppedError) return
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
    // Backend rejects questions under 3 chars — match BriefChat's guard (the
    // send buttons are also disabled below 3, this covers Enter-to-send).
    if (q.length < 3) {
      if (q.length > 0) showToast("Question too short", "Use at least 3 characters.")
      return
    }
    // Cheap active-tab guard; submitAsk re-checks per the resolved target tab.
    if (activeTabId != null && askingTabsRef.current.has(activeTabId)) return
    setDraft("")
    void submitAsk(q)
    const ta = composerRef.current
    if (ta) {
      // Clear the inline height so the textarea snaps back to its CSS resting
      // size (min-height + padding). A hardcoded value here is shorter than the
      // vertical padding and clips the placeholder after sending.
      ta.style.height = ""
    }
  }

  const filteredSkills = useMemo(
    () =>
      skills.filter((s) =>
        slashFilter === "" ||
        s.trigger.toLowerCase().includes("/" + slashFilter) ||
        s.label.toLowerCase().includes(slashFilter) ||
        s.description.toLowerCase().includes(slashFilter),
      ),
    [skills, slashFilter],
  )
  const slashOpen = showSlash && filteredSkills.length > 0
  // Keep the highlight in range as the filtered list shrinks/grows.
  useEffect(() => {
    setSlashActive((i) => Math.min(i, Math.max(0, filteredSkills.length - 1)))
  }, [filteredSkills.length])

  const handleComposerKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // When the slash palette is open, arrow keys / Enter / Tab drive it and Esc
    // dismisses it — the composer's own Enter-to-send yields to the picker.
    if (slashOpen) {
      if (e.key === "ArrowDown") {
        e.preventDefault()
        setSlashActive((i) => (i + 1) % filteredSkills.length)
        return
      }
      if (e.key === "ArrowUp") {
        e.preventDefault()
        setSlashActive((i) => (i - 1 + filteredSkills.length) % filteredSkills.length)
        return
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault()
        handleSlashSelect(filteredSkills[slashActive] ?? filteredSkills[0])
        return
      }
      if (e.key === "Escape") {
        e.preventDefault()
        setShowSlash(false)
        return
      }
    }
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
      setSlashActive(0)
    } else {
      setShowSlash(false)
    }
  }

  const handleSlashSelect = (skill: SkillInfo) => {
    setShowSlash(false)
    setDraft(skill.trigger + " ")
    composerRef.current?.focus()
  }

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
    // duplicates. We still prune OTHER disposable tabs (keep the strip clean) but
    // never the one the user is about to sit on.
    //
    // A tab is only DISPOSABLE if it carries no conversation AND no insight/PRD
    // work: a PRD/insight tab opens with an empty thread — its insight lives in
    // the opening insight card, not a thread turn — so it must survive "+" even
    // though thread.length === 0. briefMeta covers insight-bound tabs; prd/prdId/
    // evidence + the generating flags cover backlog PRD tabs (which carry no
    // briefMeta) both while generating and once the artifact has landed.
    const disposable = (t: ChatTab) =>
      t.thread.length === 0 &&
      !t.briefMeta && !t.prd && !t.prdId && !t.evidence &&
      !t.prdGenerating && !t.evidenceGenerating
    // Compute the next tabs from the ref (not inside the setTabs updater):
    // updater callbacks run later, during React's render, so an id assigned
    // inside one is still null when setActiveTabId below reads it — which left
    // the fresh "+" tab created but never activated.
    const prev = tabsRef.current
    const existingEmpty = prev.find((t) => disposable(t) && t.title === NEW_CHAT_TITLE)
    let targetId: string
    if (existingEmpty) {
      targetId = existingEmpty.id
      // Drop any OTHER disposable tabs, keep the one we're reusing.
      setTabs(prev.filter((t) => !disposable(t) || t.id === existingEmpty.id))
    } else {
      const id = `tab-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
      targetId = id
      // Prune other disposable tabs, then append the fresh "New chat" tab.
      setTabs([...prev.filter((t) => !disposable(t)), {
        id, title: NEW_CHAT_TITLE, thread: [], dbConvId: null, briefMeta: null,
        insightBody: null, prdId: null,
        prd: null, evidence: null, prdGenerating: false, evidenceGenerating: false,
      }])
    }
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

  // ── PRD deep-link (`/brief?prd=<id>`) ─────────────────────────────────────
  // The Slack "your PRD is ready" ping links here carrying the prd id. Open that
  // PRD as a chat tab + panel via the SAME load flow the command palette / brief
  // "View PRD" use (openPrdTab → kind:"load"). openPrdTab routes to `/`, which
  // strips the `?prd=` param, so no separate replace is needed. Latched like the
  // new-chat handler so it fires once per arrival and re-arms when absent.
  const consumedPrdRef = useRef(false)
  useEffect(() => {
    const raw = searchParams.get("prd")
    if (raw == null) {
      consumedPrdRef.current = false
      return
    }
    if (consumedPrdRef.current) return
    const prdId = Number(raw)
    if (!Number.isInteger(prdId) || prdId <= 0) return
    consumedPrdRef.current = true
    openPrdTab({ title: "PRD", source: { kind: "load", prdId, meta: null } })
  }, [searchParams, openPrdTab])

  const hasThread = thread.length > 0
  // A tab bound to a PRD or brief insight opens with the insight itself as the
  // conversation's first agent message (see the insight turn rendered at the top
  // of the thread) — NOT as a pinned heading above the chat. That message is what
  // anchors the chat to its insight and hosts the Generate/View PRD + prototype
  // actions, so an insight-bound tab always shows the thread view (never the
  // generic "Welcome back" landing) even before the user has sent anything.
  // Also shown while a PRD is still GENERATING (import/resume tabs carry no
  // briefMeta and no prd yet) — the card's button reads "Generating PRD…" and
  // flips to "View PRD" on landing, so the panel is always reopenable from chat.
  const showInsightMsg = !!(activeTab?.prd || activeTab?.briefMeta || activeTab?.prdGenerating)
  // A resumed tab whose history is still fetching shows the thread view (with
  // a loading skeleton) — never the "Welcome back" landing.
  const showThreadView = hasThread || showInsightMsg || !!activeTab?.hydrating
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
  const chatPrdExists = !!activeTab?.prd || activeTab?.prdId != null
    || !!(chatInsightState?.hasPrd && chatInsightState.prdId != null)
  // While the brief-prototype map is still loading we don't yet KNOW whether a
  // PRD exists, so committing to "Generate PRD" would flash the wrong label then
  // flip to "View PRD" once the map lands. Show a neutral "Loading…" until we
  // know — but only for an insight-bound tab that has no PRD loaded on it yet
  // (a tab already carrying its prd is authoritative, no wait needed).
  const chatPrdCtaWaiting = !chatPrdExists && !!activeTab?.briefMeta && chatMapLoading
  // Does the active tab's insight already have a saved evidence brief? Check once
  // per insight (cache-read only, no generation) so the chat's first action reads
  // "View Evidence" when evidence exists. Uploaded PRDs / plain chats carry no
  // insight (no briefMeta), so they fall through to the PRD action.
  const activeEvidenceKey = activeTab?.briefMeta
    ? `${activeTab.briefMeta.briefId}:${activeTab.briefMeta.insightIndex}`
    : null
  useEffect(() => {
    if (!activeEvidenceKey || checkedEvidenceRef.current.has(activeEvidenceKey)) return
    checkedEvidenceRef.current.add(activeEvidenceKey)
    const [bId, iIdx] = activeEvidenceKey.split(":").map(Number)
    let cancelled = false
    void (async () => {
      try {
        const ev = await loadEvidenceByInsight(bId, iIdx)
        if (!cancelled && ev) {
          setInsightsWithEvidence((prev) => new Set(prev).add(activeEvidenceKey))
        }
      } catch { /* non-fatal: default to the PRD action */ }
    })()
    return () => { cancelled = true }
  }, [activeEvidenceKey])
  // Evidence exists if it's cached on the tab OR the insight has a saved brief.
  const chatEvidenceExists =
    !!activeTab?.evidence || (activeEvidenceKey != null && insightsWithEvidence.has(activeEvidenceKey))
  // The PRD the chat's prototype button generates/views from (null → disabled).
  const chatProtoPrdId = activeTab?.prdId ?? chatInsightState?.prdId ?? null
  // Whether a ready prototype already exists (from the batch map) — drives the
  // prototype button's View vs Generate face.
  const chatPrototypeReady = !!chatInsightState?.prototypeReady
  // Navigate to an already-built prototype (the CTA's skipExistenceCheck path
  // only GENERATES; the batch map tells us when to VIEW instead).
  const handleViewPrototype = useCallback(() => {
    const pid = chatInsightState?.prototypePrdId ?? chatInsightState?.prdId ?? null
    if (pid != null) router.push(prototypePath(pid))
  }, [chatInsightState, router])
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

          {/* Tab bar — always visible. Browser-style: grey strip, the ACTIVE tab
              is a white card (side+top borders, rounded top corners) that merges
              with the white content area below by overlapping the strip's bottom
              border; inactive tabs are plain grey labels on the strip. */}
          <div data-testid="chat-tab-bar" style={{
            display: "flex", alignItems: "stretch", gap: 0,
            borderBottom: "1px solid var(--line, #E8E6E0)", background: "var(--surface-2, #F7F5F0)",
            height: 44, paddingLeft: 8, overflowX: "auto", overflowY: "visible", flexShrink: 0,
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
                background: isBriefTab ? "var(--surface, #fff)" : "transparent",
                borderTop: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                borderLeft: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                borderRight: isBriefTab ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                borderRadius: "8px 8px 0 0",
                marginTop: 8, marginBottom: -1,
                whiteSpace: "nowrap", transition: "color 0.12s, background 0.12s, border-color 0.12s",
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
                    background: isActive ? "var(--surface, #fff)" : "transparent",
                    borderTop: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                    borderLeft: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                    borderRight: isActive ? "1px solid var(--line, #E8E6E0)" : "1px solid transparent",
                    borderRadius: "8px 8px 0 0",
                    marginTop: 8, marginBottom: -1,
                    whiteSpace: "nowrap", transition: "color 0.12s, background 0.12s, border-color 0.12s",
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
              onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "var(--line, #E8E6E0)" }}
              onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.background = "transparent" }}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                width: 28, height: 28, margin: "8px 4px 0 6px", padding: 0,
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
            <div
              className={`od-center-scroll${!showThreadView ? " od-center-scroll--home-landing" : ""}`}
              ref={threadScrollRef}
              onScroll={handleThreadScroll}
            >
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
                        {/* Slash command palette (home) */}
                        {slashOpen && (
                          <SlashSkillMenu
                            skills={filteredSkills}
                            activeIndex={slashActive}
                            onSelect={handleSlashSelect}
                            onHover={setSlashActive}
                          />
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
                            <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.doc,.docx,.pptx" style={{ display: "none" }} onChange={handleFileSelect} />
                            <button type="button" className="chat-home-action-btn" aria-label="Attach file" onClick={() => fileInputRef.current?.click()}>
                              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
                              </svg>
                              Attach
                            </button>
                          </div>
                          {busy ? (
                            <button
                              type="button"
                              className="chat-home-composer-send chat-home-composer-send--stop"
                              aria-label="Stop generating"
                              onClick={handleStopAsk}
                            >
                              <IconStop size={14} />
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="chat-home-composer-send"
                              aria-label="Send"
                              disabled={draft.trim().length < 3}
                              onClick={handleComposerSubmit}
                            >
                              <IconSendUp size={16} />
                            </button>
                          )}
                        </div>
                        {/* Attached files preview — the landing composer must show
                            what's attached too, not rely on the transient toast */}
                        <AttachmentChips attachments={attachments} onRemove={(i) => setAttachments((p) => p.filter((_, idx) => idx !== i))} />
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
                  <div className="bc-thread" ref={setThreadContentEl}>
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
                            Product Coworker
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
                        <ChatArtifactActions
                          evidenceExists={chatEvidenceExists}
                          prdExists={chatPrdExists}
                          prdWaiting={chatPrdCtaWaiting}
                          prdGenerating={!!activeTab?.prdGenerating}
                          onViewEvidence={handleOpenEvidence}
                          onOpenPrd={handleOpenPrd}
                          prototypePrdId={chatProtoPrdId}
                          prototypeReady={chatPrototypeReady}
                          onViewPrototype={handleViewPrototype}
                        />
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
                    {/* Resumed-conversation loading state: the tab opened
                        instantly on row click; its history is still in flight. */}
                    {activeTab?.hydrating && thread.length === 0 ? (
                      <div className="bc-turn" aria-busy="true">
                        <div className="bc-agent-head">
                          <span className="bc-agent-mark">
                            <IconSparkle size={14} />
                          </span>
                          <span className="bc-agent-name">{AGENT_NAME}</span>
                          <span className="bc-agent-status">loading conversation…</span>
                        </div>
                        <div className="bc-agent-body">
                          <AssistantThinkingSkeleton compact />
                        </div>
                      </div>
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
                          {turn.attachments?.length ? (
                            <div className="bc-user-attachments">
                              {turn.attachments.map((a, i) => (
                                <TurnAttachmentCard
                                  key={i}
                                  name={a.name}
                                  content={a.content}
                                  onOpen={() =>
                                    setViewerAttachment({ name: a.name, content: a.content ?? "" })
                                  }
                                />
                              ))}
                            </div>
                          ) : null}
                          {turn.query ? <div className="bc-user-bubble">{turn.query}</div> : null}
                          <div className="bc-agent-head">
                            <span className="bc-agent-mark">
                              <IconSparkle size={14} />
                            </span>
                            <span className="bc-agent-name">{AGENT_NAME}</span>
                            <span className="bc-agent-badge">
                              <IconSparkle size={10} />
                              Product Coworker
                            </span>
                          </div>
                          <div className="bc-agent-body">
                            {turn.error ? <div className="bc-error">{turn.error}</div> : null}
                            {turn.stopped && !turn.reply ? (
                              <div className="bc-stopped">You stopped this response.</div>
                            ) : null}
                            {!turn.reply && !turn.error && !turn.stopped ? <AssistantThinkingSkeleton compact /> : null}
                            {turn.reply ? (
                              <AskReplyBody
                                reply={turn.reply}
                                animateIn={hasFreshReply}
                                simulateTyping={hasFreshReply}
                              />
                            ) : null}
                          </div>
                          {/* Skip the row when the insight/PRD card is shown at the
                              top of the thread — it already hosts these actions, and
                              rendering both reads as duplicate button noise. */}
                          {isLast && turn.reply && !showInsightMsg ? (
                            <ChatArtifactActions
                              evidenceExists={chatEvidenceExists}
                              prdExists={chatPrdExists}
                              prdWaiting={chatPrdCtaWaiting}
                              prdGenerating={!!activeTab?.prdGenerating}
                              onViewEvidence={handleOpenEvidence}
                              onOpenPrd={handleOpenPrd}
                              prototypePrdId={chatProtoPrdId}
                              prototypeReady={chatPrototypeReady}
                              onViewPrototype={handleViewPrototype}
                            />
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
                {/* Slash command palette */}
                {slashOpen && (
                  <SlashSkillMenu
                    skills={filteredSkills}
                    activeIndex={slashActive}
                    onSelect={handleSlashSelect}
                    onHover={setSlashActive}
                    inset
                  />
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
                      <input ref={fileInputRef} type="file" multiple accept=".txt,.md,.csv,.json,.pdf,.doc,.docx,.pptx" style={{ display: "none" }} onChange={handleFileSelect} />
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
                    {busy ? (
                      <button
                        type="button"
                        className="bc-send bc-send--stop"
                        aria-label="Stop generating"
                        onClick={handleStopAsk}
                      >
                        <IconStop size={16} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="bc-send"
                        aria-label="Send"
                        disabled={draft.trim().length < 3}
                        onClick={handleComposerSubmit}
                      >
                        <IconSendUp size={18} />
                      </button>
                    )}
                  </div>
                </div>
                {/* Attached files preview */}
                <AttachmentChips attachments={attachments} onRemove={(i) => setAttachments((p) => p.filter((_, idx) => idx !== i))} />
              </div>
            ) : null}
          </main>
          )}
        </div>
      </div>
      {viewerAttachment ? (
        <AttachmentViewer attachment={viewerAttachment} onClose={() => setViewerAttachment(null)} />
      ) : null}
    </AppLayout>
  )
}
