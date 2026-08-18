"use client"

/**
 * The project chat mount — main's ACTUAL chat, configured for ONE conversation.
 *
 * It composes the SAME shared unit main uses (`useComposer` + `useThreadScroll` +
 * `useMainConversation`) over a SINGLE-conversation store (a `useState`-backed
 * `ConversationHandle`, no tabs), bound to a project-scoped `conversations` row
 * resolved via the 2B resolver (`projectsApi.individualChat`/`groupChat`). It
 * returns the exact `ConversationViewProps` host-bag, so a slot renders it as
 * `<ConversationView {...useProjectConversation(...)} />` — the identical view
 * main renders per tab.
 *
 * The per-surface seams the unit takes are supplied here in their
 * SINGLE-CONVERSATION form (no tab resolution): identity, `resolveAskParams`
 * (conversation_id, NO project_id — the project chat is main chat on a
 * project-bound row, no project context), and `conversation_turns` persistence.
 *
 * FUNCTIONAL-FIRST SCOPE (this pass): the send/stream/settle/stop core, history
 * resume, scroll, and the composer are wired. Command-intent dispatch (the
 * `useConversationGeneration` flows behind the intent envelope) and the dock
 * popups (assign/share) are DEFERRED to a follow-up wiring pass — flagged inline;
 * the extracted generation hook + its seams are ready, only the project
 * `submitAsk`'s intent-envelope dispatch remains to be wired.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { useCompany } from "../../../../context/CompanyContext"
import { createChatPersistence, replyToText } from "../../../../lib/chatPersistence"
import { conversationsApi, projectsApi, type AskResponse } from "../../../../lib/api"
import { resolveAttachmentRefs } from "../../../shared/chatComposerController"
import { useNextPrompts, type NextPromptsAdapter } from "../../../shared/chat-shell/useNextPrompts"
import { useComposer } from "../useComposer"
import { useThreadScroll } from "../useThreadScroll"
import { useMainConversation } from "../useMainConversation"
import type { ConversationHandle, AskGrounding } from "../conversationCore"
import type { ThreadTurn } from "../ChatScreen"
import type { ConversationViewProps } from "../ConversationView"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"

export type ProjectChatSurface = "individual" | "group"

/** The stable local key for a project mount's single conversation. Distinct per
 *  surface so the ask/poll/resume spine (which keys on it) never collides
 *  between the individual and group slots of the same project. */
function surfaceKey(projectId: number | string, surface: ProjectChatSurface): string {
  return `project-${projectId}-${surface}`
}

export function useProjectConversation(
  projectId: number | string,
  surface: ProjectChatSurface,
): ConversationViewProps {
  const convKey = useMemo(() => surfaceKey(projectId, surface), [projectId, surface])
  const { activeCompany } = useCompany()
  const { profile } = useWorkspace()
  const name = profileDisplayName(profile) || "You"
  const userInitials = name.slice(0, 2).toUpperCase()

  // ── The single-conversation store ─────────────────────────────────────────
  const [thread, setThread] = useState<ThreadTurn[]>([])
  const [dbConvId, setDbConvId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  const threadRef = useRef<ThreadTurn[]>(thread)
  threadRef.current = thread
  const dbConvIdRef = useRef<number | null>(null)
  dbConvIdRef.current = dbConvId
  const busyRef = useRef(false)
  busyRef.current = busy
  const stoppedRef = useRef(false)
  const askingRef = useRef<Set<string>>(new Set())
  // The engine's runTabAsk toggles a busy Set (main has one per tab); collapse
  // it to this one conversation's boolean, keyed off a dedicated set so it never
  // aliases the asking set.
  const busySetRef = useRef<Set<string>>(new Set())
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const askStartRef = useRef<Map<string, number>>(new Map())
  const resumedTurnsRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  // ── Resolve the project conversation row + hydrate its history ─────────────
  useEffect(() => {
    let cancelled = false
    setHydrating(true)
    ;(async () => {
      try {
        const conv = surface === "group"
          ? await projectsApi.groupChat(projectId)
          : await projectsApi.individualChat(projectId)
        if (cancelled) return
        setDbConvId(conv.id)
        const { turns } = await conversationsApi.listTurns(conv.id)
        if (cancelled) return
        // Fold the stored role/content rows into the ThreadTurn (Q→reply) model,
        // the same shape main's resume builds.
        const restored: ThreadTurn[] = []
        for (let i = 0; i < (turns ?? []).length; i++) {
          const t = turns[i]
          if (t.role === "user") {
            const next = turns[i + 1]
            const reply = next?.role === "assistant"
              ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
              : undefined
            restored.push({ id: `resumed-${conv.id}-${i}`, query: t.content, reply })
            if (reply) i++
          } else if (t.role === "assistant" && t.content.trim()) {
            restored.push({ id: `resumed-${conv.id}-${i}`, query: "", reply: { answer: t.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse })
          }
        }
        restored.forEach((r) => resumedTurnsRef.current.add(r.id))
        if (restored.length) setThread(restored)
      } catch {
        /* leave empty — a fresh/failed resolve opens an empty chat */
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => { cancelled = true }
  }, [projectId, surface])

  // ── Persistence via conversation_turns (server-only writes) ────────────────
  const persistence = useMemo(() => createChatPersistence({
    getApi: () => import("../../../../lib/api").then((m) => m.conversationsApi),
    getTabConvId: () => dbConvIdRef.current,
    getTabPrdId: () => null,
    setTabConvId: (_key, convId) => { setDbConvId(convId) },
    onConversationCreated: () => { /* no rail entry on a project surface */ },
  }), [])

  // ── The single-conversation handle ────────────────────────────────────────
  const makeHandle = useCallback((_key: string): ConversationHandle => ({
    key: convKey,
    getTurns: () => threadRef.current,
    patchTurns: (update) => setThread((prev) => {
      const next = update(prev)
      return next === prev ? prev : next
    }),
    setBusy: (b) => setBusy(b),
    markStopped: () => { stoppedRef.current = true },
    isStopped: () => stoppedRef.current,
    clearAsking: () => { askingRef.current.delete(convKey) },
    pendingAsk: () => null,
    isAsking: () => askingRef.current.has(convKey),
    exists: () => true,
    patchMeta: () => { /* project chat has no per-conversation artifact meta yet */ },
    isActive: () => true,
    dbConvId: () => dbConvIdRef.current,
    getMeta: () => null,
  }), [convKey])

  // The one surface-divergent grounding seam: conversation_id, NO project_id.
  const resolveAskParams = useCallback(async (
    key: string,
    meta: { turnId: string; displayQuery: string },
  ): Promise<{ convId: number | null; grounding: AskGrounding }> => {
    const convId = dbConvIdRef.current ?? await persistence.ensureConversation(key, {
      turnId: meta.turnId,
      title: meta.displayQuery.length > 52 ? `${meta.displayQuery.slice(0, 49)}…` : meta.displayQuery,
      query: meta.displayQuery,
    })
    return {
      convId: convId ?? null,
      grounding: convId != null ? { conversation_id: convId } : {},
    }
  }, [persistence])

  // Optimistic pending-conversation rail entry has no project analogue — persist
  // only (mirrors main's create-once, minus the rail).
  const pushPendingConversation = useCallback((
    turnId: string, query: string, key: string,
    attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[],
  ) => {
    const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
    void persistence.pushUserTurn(key, { turnId, title, query, attachments })
  }, [persistence])

  const finalizeConversationTurn = useCallback((
    turnId: string, updates: { reply?: AskResponse; error?: string }, key: string,
  ): Promise<void> => {
    if (updates.reply) return persistence.pushAssistantTurn(key, replyToText(updates.reply))
    return Promise.resolve()
  }, [persistence])

  const nextPromptsAdapter: NextPromptsAdapter = useMemo(() => ({
    fetchSuggestions: async () => [],
  }), [])
  const nextPrompts = useNextPrompts(nextPromptsAdapter)

  // ── Compose the shared unit ────────────────────────────────────────────────
  const composer = useComposer({ showToast: () => { /* project mount has no toast surface yet */ } })
  const scroll = useThreadScroll({ thread, activeTabId: convKey, pendingSend: composer.pendingSend })
  const engine = useMainConversation({
    makeHandle,
    activeKey: convKey,
    activeCompany,
    askingRef,
    setBusy: (updater) => {
      const next = updater(busySetRef.current)
      busySetRef.current = new Set(next)
      setBusy(next.has(convKey))
    },
    resolveAskParams,
    getPrdId: () => null,
    mountedRef,
    animatedTurnIds,
    askStartRef,
    resumedTurnsRef,
    pushPendingConversation,
    setActiveConv: () => { /* no rail */ },
    finalizeConversationTurn,
    nextPrompts,
    showToast: () => {},
  })

  // ── The single-conversation submit (plain-ask core) ────────────────────────
  // FUNCTIONAL-FIRST: seeds the optimistic turn, extracts attachments, runs the
  // shared ask. Command-intent dispatch (the useConversationGeneration flows
  // behind chatIntentApi) is the next wiring step — a bare command is answered
  // as a question until it lands, exactly as main's ask does on an intent-call
  // failure. No re-derivation: it will reuse the shared dispatch + flows.
  const submitAsk = useCallback(async (rawQuery: string) => {
    const trimmed = rawQuery.trim()
    if (trimmed.length < 1 && composer.attachments.length === 0) return
    nextPrompts.retire(convKey)
    const askStartedAt = Date.now()
    composer.setPendingSend({
      tabId: convKey,
      query: trimmed,
      attachments: composer.attachments.map((a) => ({ name: a.name })),
      startedAt: askStartedAt,
    })
    if (askingRef.current.has(convKey)) { composer.setPendingSend(null); return }
    const id = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`
    const hasAttachments = composer.attachments.length > 0
    const displayQuery = trimmed
    const newTurn: ThreadTurn = {
      id, query: displayQuery,
      ...(hasAttachments ? { attachments: composer.attachments.map((a) => ({ name: a.name })) } : {}),
    }
    setThread((prev) => [...prev, newTurn])
    composer.setPendingSend(null)
    askStartRef.current.set(id, askStartedAt)
    stoppedRef.current = false
    let sendQuery = displayQuery
    let persistedAttachments: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | undefined
    if (hasAttachments) {
      setBusy(true)
      try {
        const extracted = await resolveAttachmentRefs(composer.attachments)
        const ctx = extracted.map((e) => `--- ${e.name} ---\n${e.content}`).join("\n\n").slice(0, 100000)
        persistedAttachments = extracted.map((e) => ({ name: e.name, content: e.content, key: e.key, mime: e.mime, size: e.size }))
        setThread((prev) => prev.map((t) => t.id === id ? { ...t, attachments: persistedAttachments } : t))
        sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
        composer.setAttachments([])
      } catch {
        setBusy(false)
        setThread((prev) => prev.filter((t) => t.id !== id))
        return
      }
    }
    await engine.runConversationAsk({ targetTabId: convKey, id, displayQuery, sendQuery, persistedAttachments })
  }, [convKey, composer, engine, nextPrompts])

  // Composer submit/keydown wired to the project submitAsk (main leaves these in
  // its host; here they are the mount's own single-conversation bridge).
  const handleComposerSubmit = useCallback(() => {
    const q = composer.draft.trim()
    if (q.length < 1 && composer.attachments.length === 0) return
    if (composer.voice.listening) composer.voice.cancel()
    composer.setDraft("")
    composer.setPinnedSkill(null)
    composer.setPlusMenuOpen(false)
    void submitAsk(q)
  }, [composer, submitAsk])
  const handleComposerKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (composer.slashOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); composer.setSlashActive((i) => (i + 1) % composer.filteredSkills.length); return }
      if (e.key === "ArrowUp") { e.preventDefault(); composer.setSlashActive((i) => (i - 1 + composer.filteredSkills.length) % composer.filteredSkills.length); return }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); composer.handleSlashSelect(composer.filteredSkills[composer.slashActive] ?? composer.filteredSkills[0]); return }
      if (e.key === "Escape") { e.preventDefault(); composer.setShowSlash(false); composer.setSlashFromMenu(false); return }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleComposerSubmit() }
  }, [composer, handleComposerSubmit])

  // ── Assemble the host-bag ──────────────────────────────────────────────────
  const activeTab = useMemo(() => ({ id: convKey, hydrating: hydrating && thread.length === 0 }), [convKey, hydrating, thread.length])
  const lastLiveTurnIdx = thread.length - 1
  const composerHintNode = composer.voice.error ? composer.voice.error : null

  const mapDeps: MapMainTurnsDeps = useMemo(() => ({
    animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
    busy, activeTab: { id: convKey, prdId: null, prd: null, prdGenerating: false },
    name, userInitials, skillForQuery: composer.skillForQuery,
    // Generation/artifact CTA state — DEFERRED (no per-conversation artifacts on
    // the project surface in this pass); inert defaults keep the transcript
    // rendering exactly as a plain chat.
    ticketSetActionState: null, showInsightMsg: false, chatEvidenceExists: false,
    chatPrdExists: false, chatPrdCtaWaiting: false, chatProtoPrdId: null, chatPrototypeReady: false,
    inlinePrdCards: false, inlinePrdAnchorIdx: null, insightCardNode: null, prdQuestionsNode: null,
    clarifyPopupOpen: false, pendingClarifyTurn: null,
    handleAskAgain: () => {}, handleStopAsk: engine.handleStopAsk,
    submitClarifyAnswers: () => {}, setViewerAttachment: () => {},
    openReportByTitle: () => {}, openArtifactInPanel: () => {}, openChatArtifactItem: () => {},
    handleTicketSetAction: () => {}, handleOpenEvidence: () => {}, handleOpenPrd: () => {},
    handleViewPrototype: () => {}, handlePrototypeSettled: () => {},
    onSendSlackShare: () => {}, onCancelSlackShare: () => {}, onPickSlackShareTarget: () => {},
  }), [lastLiveTurnIdx, busy, convKey, name, userInitials, composer.skillForQuery, engine.handleStopAsk])

  const showThreadView = thread.length > 0 || !!activeTab.hydrating || (!!composer.pendingSend && composer.pendingSend.tabId === convKey)

  return {
    thread,
    mapDeps,
    draft: composer.draft,
    pinnedSkill: composer.pinnedSkill,
    attachments: composer.attachments,
    composerHintNode,
    plusMenuOpen: composer.plusMenuOpen,
    plusMenuActive: composer.plusMenuActive,
    slashOpen: composer.slashOpen,
    filteredSkills: composer.filteredSkills,
    slashActive: composer.slashActive,
    composerRef: composer.composerRef,
    fileInputRef: composer.fileInputRef,
    voice: composer.voice,
    handleSlashSelect: composer.handleSlashSelect,
    setSlashActive: composer.setSlashActive,
    handleComposerInput: composer.handleComposerInput,
    handleComposerKeyDown,
    handleComposerSubmit,
    setPlusMenuActive: composer.setPlusMenuActive,
    setPlusMenuOpen: composer.setPlusMenuOpen,
    handlePlusMenuSelect: composer.handlePlusMenuSelect,
    setAttachments: composer.setAttachments,
    setPinnedSkill: composer.setPinnedSkill,
    handleFileSelect: composer.handleFileSelect,
    handleToggleVoice: composer.handleToggleVoice,
    // Landing — a project chat opens straight into the thread view; no home cards.
    showChipRow: false,
    displayChips: [],
    handleHomeCard: () => {},
    handleStarterChip: (text) => { void submitAsk(text) },
    showEmptyStarters: false,
    activeTab,
    pendingSendHere: !!composer.pendingSend && composer.pendingSend.tabId === convKey,
    pendingSend: composer.pendingSend,
    // Dock popups (clarify/assign/share) — DEFERRED with command dispatch.
    pendingClarifyTurn: null,
    setClarifyPopupDismissed: () => {},
    assignPopupOpen: false,
    pendingAssignState: undefined,
    activeTabId: convKey,
    completeAssign: () => {},
    cancelAssign: () => {},
    sharePopupOpen: false,
    pendingShareState: undefined,
    completeShareQuestion: () => {},
    cancelShareQuestion: () => {},
    setQuestionDockEl: () => {},
    nextPrompts,
    submitAsk,
    showThreadView,
    threadScrollRef: scroll.threadScrollRef,
    handleThreadScroll: scroll.handleThreadScroll,
    setThreadContentEl: scroll.setThreadContentEl,
  }
}
