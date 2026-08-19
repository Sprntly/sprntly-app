"use client"

/**
 * The project chat mount — main's ACTUAL chat, configured for ONE conversation.
 *
 * Composes the SAME shared unit main uses (`useComposer` + `useThreadScroll` +
 * `useMainConversation` + `useConversationGeneration`) over a SINGLE-conversation
 * store (a `useState`-backed `ConversationHandle`, no tabs), bound to a
 * project-scoped `conversations` row (2B resolver). Returns the exact
 * `ConversationViewProps` host-bag, so a slot renders it as
 * `<ConversationView {...useProjectConversation(...)} />` — main's identical view.
 *
 * COMMAND/GENERATION HALF (Tier 1): `submitAsk` runs the SAME shared intent path
 * main does — `chatIntentApi.resolve` → `dispatchChatIntent` → the shared
 * `useConversationGeneration` flows + action layer — over SINGLE-CONVERSATION
 * seams (single-conv `emitTurn`/`seedGenerationTurn`, the SAME global
 * `ContentPanel`, `resolveAskParams` with conversation_id + NO project_id, no-op
 * ticket-set/summary seams). Generate-PRD + the clarify/sufficiency wizard run a
 * thin single-conversation opener over `lib/runPrdGeneration` (the PRD tab-wiring
 * stays main-only by design).
 *
 * The assign/share DOCK is fully wired: parking + render + cancel + the apply
 * (completeAssign → PUT /fields) and channel re-preview / send (completeShare-
 * Question + the Slack card's send/pick/cancel) all mirror main over this single
 * conversation. Import-from-doc PRD tab wiring stays main-only by design; the
 * AttachmentViewer + report-by-title opens have no project-surface sink yet.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { createChatPersistence, replyToText } from "../../../../lib/chatPersistence"
import {
  conversationsApi, prdApi, chatIntentApi, askApi, chatSuggestionsApi, projectsApi,
  slackShareApi, ticketDataApi, customArtifactsApi,
  type AskResponse, type ChatIntentEnvelope, type OpenArtifactCandidate, type TicketAssignQuestion,
  type ChatArtifactItem, type SlackShareTarget, type SlackShareTargetRef,
} from "../../../../lib/api"
import { slackShareQuestionFor } from "../../../../lib/chat/slackShareQuestion"
import { type PopupAnswer } from "../../../shared/QuestionPopup"
import { resumePrdGeneration } from "../../../../lib/runPrdGeneration"
import { getPendingAsk, resumeAskGeneration, AskCancelledError, AskStoppedError, AskTimeoutError } from "../../../../lib/runAskGeneration"
import { resolveAttachmentRefs } from "../../../shared/chatComposerController"
import { dispatchChatIntent } from "../../../../lib/chat/dispatchChatIntent"
import { useChatIntentExecutors } from "../../../shared/chat-shell/useChatIntentExecutors"
import { runEditPrdAction, runShareToSlackAction, runAssignTicketsAction } from "../../../shared/chat-shell/conversation/actions"
import { useNextPrompts, type NextPromptsAdapter } from "../../../shared/chat-shell/useNextPrompts"
import { DEFAULT_HOME_CHIPS } from "../../../../lib/homeChips"
import { type ClarifyAnswer, clarifyQuestionsText } from "../../../shared/ClarifyQuestionsCard"
import { useComposer } from "../useComposer"
import { useThreadScroll } from "../useThreadScroll"
import { useMainConversation } from "../useMainConversation"
import { useConversationGeneration } from "../useConversationGeneration"
import type { ConversationHandle, AskGrounding } from "../conversationCore"
import type { ThreadTurn, ChatTab } from "../ChatScreen"
import type { ConversationViewProps } from "../ConversationView"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"

export type ProjectChatSurface = "individual" | "group"

/** The attachment overlay's state shape — main keeps this on ChatScreen; the
 *  project surface owns its own copy and hands it to the host to render the
 *  SHARED `AttachmentViewer`. */
export type ViewerAttachment = { name: string; content: string; key?: string | null; mime?: string | null }

/** The host-bag `useProjectConversation` returns: the exact `ConversationViewProps`
 *  main renders, plus the project surface's own attachment-viewer state so its
 *  host can mount the shared `AttachmentViewer` alongside `ConversationView`
 *  (main renders that component at its own root instead — see ChatScreen). */
export type ProjectConversationProps = ConversationViewProps & {
  viewerAttachment: ViewerAttachment | null
  setViewerAttachment: (a: ViewerAttachment | null) => void
}

type PendingClarify = { task: string; sourceDocs?: { name: string; content: string }[]; turnId: string }
type PendingAssign = { questions: TicketAssignQuestion[]; applied: string[]; turnId: string }
type PendingShare = { turnId: string; kind: "channel" | "target"; header: string; prompt: string; options: { label: string; description?: string | null; value: string }[] }

/** Answers → the same prose the clarify card records (the popup pre-filters
 *  skipped/blank before calling in, so every entry here carries an answer). */
function clarifyAnswersText(answers: ClarifyAnswer[]): string {
  return answers
    .filter((a) => a.answer)
    .map((a) => `${a.prompt}: ${a.answer}`)
    .join("\n")
}
const CLARIFY_SKIP_RE = /^\s*(generate( now)?|go|proceed|do it|just do it|skip|that'?s? (all|it|enough))\b/i

function surfaceKey(projectId: number | string, surface: ProjectChatSurface): string {
  return `project-${projectId}-${surface}`
}

// The project chat's empty-state landing chips: main's shared default set minus
// the "home" brief chip (its handler navigates to workspace-only screens, which
// have no project-surface equivalent). Each starter chip submits its prompt
// through the adapter's `submitAsk` via `handleStarterChip`.
const PROJECT_LANDING_CHIPS = DEFAULT_HOME_CHIPS.filter((c) => c.kind === "starter")

const newId = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`)

export function useProjectConversation(
  projectId: number | string,
  surface: ProjectChatSurface,
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void,
): ProjectConversationProps {
  const convKey = useMemo(() => surfaceKey(projectId, surface), [projectId, surface])
  const { activeCompany } = useCompany()
  const { profile } = useWorkspace()
  const { content, setContent } = useContent()
  const { openContentPanel, contentPanelTab, showToast } = useNavigation()
  const name = profileDisplayName(profile) || "You"
  const userInitials = name.slice(0, 2).toUpperCase()

  // ── The single-conversation store ─────────────────────────────────────────
  const [thread, setThread] = useState<ThreadTurn[]>([])
  const [dbConvId, setDbConvId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  // Per-conversation artifact metadata (PRD/ticket-set/clarify state) — the slice
  // the generation flows write via the handle's patchMeta and read via getMeta.
  const [meta, setMeta] = useState<Partial<ChatTab>>({})
  const [pendingClarify, setPendingClarify] = useState<PendingClarify | null>(null)
  const [pendingAssign, setPendingAssign] = useState<PendingAssign | undefined>(undefined)
  const [pendingShare, setPendingShare] = useState<PendingShare | undefined>(undefined)
  // The attachment overlay's own state — the project surface's copy of the
  // state main keeps on ChatScreen. The click handler (mapDeps.setViewerAttachment)
  // sets it; the host (ProjectMainThread) renders the SHARED AttachmentViewer from
  // it, mirroring how ChatScreen mounts the same component at its own root.
  const [viewerAttachment, setViewerAttachment] = useState<ViewerAttachment | null>(null)
  const [clarifyPopupDismissed, setClarifyPopupDismissed] = useState<Record<string, boolean>>({})
  const [questionDockEl, setQuestionDockEl] = useState<HTMLDivElement | null>(null)
  const threadRef = useRef<ThreadTurn[]>(thread)
  threadRef.current = thread
  const metaRef = useRef(meta)
  metaRef.current = meta
  const dbConvIdRef = useRef<number | null>(null)
  dbConvIdRef.current = dbConvId
  // Late-bound handle to `ensureProjectConv` (defined below) so the persistence
  // memo can delegate its create path to the durable project conversation
  // without depending on the callback's identity. Assigned during render, read
  // only inside async persistence calls, so it is always set by call time.
  const ensureProjectConvRef = useRef<(() => Promise<number | null>) | null>(null)
  // Fresh reads for the dock-question async handlers (main reads these off its
  // tabsRef; the single-conversation store keeps them on refs the same way).
  const pendingShareRef = useRef(pendingShare)
  pendingShareRef.current = pendingShare
  const pendingAssignRef = useRef(pendingAssign)
  pendingAssignRef.current = pendingAssign
  const stoppedRef = useRef(false)
  const askingRef = useRef<Set<string>>(new Set())
  const busySetRef = useRef<Set<string>>(new Set())
  const animatedTurnIds = useRef<Set<string>>(new Set())
  const askStartRef = useRef<Map<string, number>>(new Map())
  const resumedTurnsRef = useRef<Set<string>>(new Set())
  const mountedRef = useRef(true)
  // Reset true on SETUP (not only false on cleanup) — StrictMode's dev
  // mount→cleanup→remount would otherwise leave it false and cancel every ask.
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // ── Resolve the project conversation row + hydrate its history ─────────────
  useEffect(() => {
    let cancelled = false
    setHydrating(true)
    ;(async () => {
      try {
        const conv = surface === "group"
          ? await import("../../../../lib/api").then((m) => m.projectsApi.groupChat(projectId))
          : await import("../../../../lib/api").then((m) => m.projectsApi.individualChat(projectId))
        if (cancelled) return
        setDbConvId(conv.id)
        const { turns } = await conversationsApi.listTurns(conv.id)
        if (cancelled) return
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
        // Only fill a still-empty thread — never clobber one a send already started.
        if (restored.length) setThread((prev) => (prev.length === 0 ? restored : prev))
      } catch {
        /* leave empty */
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => { cancelled = true }
  }, [projectId, surface])

  // ── Mirror this conversation into the shared content store (main parity) ────
  // Main's ChatScreen stamps `content.conversationId` with the active tab's
  // conversation id; the shared `useThreadReportsSync` (AppShell) reads it to
  // fetch that thread's captured reports, and the global `ContentPanel` gates
  // its Reports tab on `threadReportsConversationId === conversationId`. The
  // project surface never set it, so a report answer here had no reports list
  // to open into. Stamp it with THIS chat's durable row so the same shared
  // report path lights up, and clear it on unmount/surface-swap so a stale
  // project conversation can never claim another surface's reports.
  useEffect(() => {
    if (dbConvId == null) return
    setContent({ conversationId: dbConvId })
    return () => { setContent({ conversationId: null }) }
  }, [dbConvId, setContent])

  // ── A thread that produced a DOCUMENT reopens on it (main parity) ───────────
  // Mirror of ChatScreen's document-reopen probe ("A thread that produced a
  // DOCUMENT opens on it") for this surface's ONE conversation. `useThreadDocumentSync`
  // (AppShell) re-attaches `content.documentId` after a reload, but nothing opens
  // the panel and a document turn has no reply-footer button, so a chat-written
  // document was reachable only from the Artifacts library once the page reloaded.
  // This probe closes that: on load, once per conversation, surface the newest
  // non-failed document into the shared panel — with the SAME precedence main
  // keeps (a PRD or ticket set that owns the panel wins, a failed doc never auto-
  // opens, an already-open panel is never fought, and a fresh in-flight generate —
  // `content.documentId` already set — is never overwritten by this older list read).
  const documentProbedRef = useRef<Set<number>>(new Set())
  useEffect(() => {
    if (hydrating || dbConvId == null) return
    const convId = dbConvId
    if (documentProbedRef.current.has(convId)) return
    const m = metaRef.current
    if (m.prd || m.prdGenerating || m.prdId != null) return
    if (m.ticketSetId != null) return
    documentProbedRef.current.add(convId)
    void (async () => {
      try {
        const docs = await customArtifactsApi.listForConversation(convId).catch(() => [])
        if (!docs.length) return
        const newest = docs[0]
        // The user may have swapped surfaces during the round trip.
        if (dbConvIdRef.current !== convId) return
        if (newest.status === "failed") return
        // Never fight a panel that is already open, and never overwrite the
        // document a live generate just wrote (the stale-read guard).
        if (contentPanelTab) return
        if (content.documentId != null) return
        setContent({ documentId: newest.id, documentGenerating: newest.status === "generating" })
        openContentPanel("document")
      } catch {
        // A resume probe must never throw — its only job is to surface an
        // artifact that may not exist.
      }
    })()
  }, [hydrating, dbConvId, contentPanelTab, content.documentId, setContent, openContentPanel])

  // ── Persistence via conversation_turns (server-only writes) ────────────────
  const persistence = useMemo(() => {
    const base = createChatPersistence({
      getApi: () => import("../../../../lib/api").then((m) => m.conversationsApi),
      getTabConvId: () => dbConvIdRef.current,
      getTabPrdId: () => metaRef.current.prdId ?? null,
      setTabConvId: (_key, convId) => { setDbConvId(convId) },
      onConversationCreated: () => { /* no rail entry on a project surface */ },
    })
    // Route the create path to the DURABLE project conversation. The shared gen
    // flows (create-artifact/import-PRD/ticket-set) call `ensureConversation`
    // when a tab has no bound row; the generic path mints a THROWAWAY
    // `conversationsApi.create` row, which the reload-time hydrate — reading the
    // project row — can't see, so a chat-written document orphans from its
    // thread (the same fork the top-of-file note calls out for sends). Delegate
    // to `ensureProjectConv` (get-or-create per project+surface) so every such
    // artifact attaches to the one row hydrate reads back.
    return {
      ...base,
      ensureConversation: () =>
        (ensureProjectConvRef.current ? ensureProjectConvRef.current() : Promise.resolve(null)),
    }
  }, [])

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
    // Same as main's handle: the persisted in-flight ask_id, keyed by this
    // conversation. handleStopAsk reads it to cancel the server job by id, and
    // the mount-time resume effect reads it to re-attach after a reload.
    pendingAsk: () => getPendingAsk(activeCompany, convKey),
    isAsking: () => askingRef.current.has(convKey),
    exists: () => true,
    patchMeta: (partial) => setMeta((prev) => ({ ...prev, ...partial })),
    isActive: () => true,
    dbConvId: () => dbConvIdRef.current,
    // The conversation's per-conv metadata as a ChatTab (the flows read
    // prd/prdId/prdGenerating/ticketSet*/pendingClarify off it).
    getMeta: () => ({
      id: convKey, title: "", thread: threadRef.current, dbConvId: dbConvIdRef.current,
      briefMeta: null, insightBody: null, prd: null, prdId: null, evidence: null,
      prdGenerating: false, evidenceGenerating: false, ...metaRef.current,
    } as ChatTab),
  }), [convKey, activeCompany])

  // The project chat's ONE durable conversation row — the SAME idempotent
  // resolver hydrate uses (get-or-create per project+surface). Every send routes
  // its conversation id through here so a turn is NEVER written to a throwaway
  // `conversationsApi.create` row (the old `persistence.ensureConversation`
  // fallback) that the reload-time hydrate — which reads the project row — can't
  // see. That fork is what made a mid-stream reload lose the whole turn, and made
  // next-prompt suggestions query the wrong/empty conversation. Sets the ref
  // synchronously so pushUserTurn/resolveAskParams, which run right after in the
  // same tick, read it without waiting on the setState render.
  const ensureProjectConv = useCallback(async (): Promise<number | null> => {
    if (dbConvIdRef.current != null) return dbConvIdRef.current
    try {
      const conv = surface === "group"
        ? await projectsApi.groupChat(projectId)
        : await projectsApi.individualChat(projectId)
      dbConvIdRef.current = conv.id
      setDbConvId(conv.id)
      return conv.id
    } catch {
      return null
    }
  }, [projectId, surface])
  // Publish for the persistence create-path override (declared above the memo).
  ensureProjectConvRef.current = ensureProjectConv

  // conversation_id, NO project_id (main chat on a project-bound row).
  const resolveAskParams = useCallback(async (
    _key: string, _m: { turnId: string; displayQuery: string },
  ): Promise<{ convId: number | null; grounding: AskGrounding }> => {
    const convId = dbConvIdRef.current ?? await ensureProjectConv()
    return { convId: convId ?? null, grounding: convId != null ? { conversation_id: convId } : {} }
  }, [ensureProjectConv])

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

  // Same adapter as main (MAIN_NEXT_PROMPTS_ADAPTER): the shared next-prompts
  // hook drives the fetch off the conversation's db id; the project surface just
  // supplies the real backend call instead of the empty stub it shipped with.
  const nextPromptsAdapter: NextPromptsAdapter = useMemo(() => ({
    fetchSuggestions: (conversationId, opts) =>
      chatSuggestionsApi.next(conversationId, opts).then((r) => r.suggestions),
  }), [])
  const nextPrompts = useNextPrompts(nextPromptsAdapter)

  // ── The shared unit ────────────────────────────────────────────────────────
  const composer = useComposer({ showToast })
  const scroll = useThreadScroll({ thread, activeTabId: convKey, pendingSend: composer.pendingSend })
  const engine = useMainConversation({
    makeHandle, activeKey: convKey, activeCompany, askingRef,
    setBusy: (updater) => {
      const next = updater(busySetRef.current)
      busySetRef.current = new Set(next)
      setBusy(next.has(convKey))
    },
    resolveAskParams,
    getPrdId: () => metaRef.current.prdId ?? null,
    mountedRef, animatedTurnIds, askStartRef, resumedTurnsRef,
    pushPendingConversation, setActiveConv: () => {}, finalizeConversationTurn,
    nextPrompts, showToast,
  })

  // ── Single-conversation generation seams + the shared flows ────────────────
  const emitTurn = useCallback((turn: ThreadTurn) => {
    setThread((prev) => [...prev, turn])
    pushPendingConversation(turn.id, turn.query, convKey)
    if (turn.reply) void finalizeConversationTurn(turn.id, { reply: turn.reply }, convKey)
  }, [convKey, pushPendingConversation, finalizeConversationTurn])

  const seedGenerationTurn = useCallback((seedTurn: ThreadTurn): { tabId: string; dbConvId: number | null } => {
    setThread((prev) => [...prev, seedTurn])
    pushPendingConversation(seedTurn.id, seedTurn.query, convKey)
    if (seedTurn.reply) void finalizeConversationTurn(seedTurn.id, { reply: seedTurn.reply }, convKey)
    return { tabId: convKey, dbConvId: dbConvIdRef.current }
  }, [convKey, pushPendingConversation, finalizeConversationTurn])

  const threadContextFor = useCallback((): string => {
    const parts: string[] = []
    for (const turn of threadRef.current) {
      if (turn.query) parts.push(`Q: ${turn.query}`)
      if (turn.reply?.answer) parts.push(`A: ${turn.reply.answer}`)
    }
    const joined = parts.join("\n\n")
    return joined.length <= 12_000 ? joined : `…\n\n${joined.slice(-12_000)}`
  }, [])

  const openArtifactInPanel = useCallback((candidate: OpenArtifactCandidate): boolean => {
    if (!onOpenArtifact) return false
    onOpenArtifact(candidate)
    return true
  }, [onOpenArtifact])

  const postOpenArtifactReply = useCallback((seedQuery: string, answer: string, candidates: OpenArtifactCandidate[]) => {
    emitTurn({
      id: newId(), query: seedQuery,
      reply: { answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse,
      ...(candidates.length ? { openCandidates: candidates } : {}),
    })
  }, [emitTurn])

  const gen = useConversationGeneration({
    emitTurn, makeHandle, seedGenerationTurn, threadContextFor, persistence,
    pushPendingConversation, finalizeConversationTurn,
    setContent, openContentPanel, content, showToast,
    openArtifactInPanel, postOpenArtifactReply,
    markTicketSetAutoOpened: () => {}, postSummary: () => {},
  })

  // ── Skills palette: the company's own uploaded skills (main parity) ─────────
  // Same fetch main runs on mount; an empty result correctly yields an empty
  // palette rather than dead built-in triggers the backend won't honour.
  useEffect(() => {
    askApi.skills().then((r) => composer.setSkills(r.skills)).catch(() => composer.setSkills([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Resume an orphaned in-flight ASK across reload (single-conversation) ────
  // Mirrors main's per-tab resume effect for this one conversation. A chat Ask
  // is fire-and-forget: the ask_id is persisted (askScope(convKey)) but the
  // awaiting poll + asking/busy markers are not. Once hydration has restored the
  // still-awaiting user turn, re-attach the poll by id (NOT re-POST) and restore
  // the "asking…" UX. Runs once per mount.
  const resumedAskRef = useRef(false)
  useEffect(() => {
    if (hydrating || resumedAskRef.current) return
    const pending = getPendingAsk(activeCompany, convKey)
    if (!pending) return
    const askId = Number(pending.id)
    if (!Number.isFinite(askId)) return
    // Re-attach only when the last turn is still awaiting a reply — the marker
    // that survives in the persisted (hydrated) thread.
    const last = threadRef.current[threadRef.current.length - 1]
    if (!last || last.reply !== undefined || last.error !== undefined || last.stopped) return
    if (askingRef.current.has(convKey)) return
    resumedAskRef.current = true
    const turnId = last.id
    // Restore the optimistic asking/busy UX for this conversation.
    askingRef.current.add(convKey)
    busySetRef.current = new Set(busySetRef.current).add(convKey)
    setBusy(true)
    stoppedRef.current = false
    resumedTurnsRef.current.add(turnId)
    askStartRef.current.set(turnId, Date.now())
    const patchTurn = (updater: (t: ThreadTurn) => ThreadTurn) =>
      setThread((prev) => prev.map((t) => (t.id === turnId ? updater(t) : t)))
    void (async () => {
      try {
        const res = await resumeAskGeneration(
          askId, activeCompany, convKey,
          () => !mountedRef.current,
          () => stoppedRef.current,
          // Re-attached mid-generation: the stream's replay frame catches the
          // preview up with everything already written, then live deltas.
          (text) => patchTurn((t) => (!t.reply && !t.stopped ? { ...t, partial: text, streamDropped: false } : t)),
          () => patchTurn((t) => (!t.reply && !t.stopped ? { ...t, streamDropped: true } : t)),
        )
        // If it streamed, mark animated BEFORE the reply lands so the typewriter
        // doesn't re-reveal text already read (main's `hasFreshReply` reasoning).
        const streamed = threadRef.current.find((t) => t.id === turnId)
        if (streamed?.partial) animatedTurnIds.current.add(turnId)
        patchTurn((t) => ({ ...t, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined }))
        void finalizeConversationTurn(turnId, { reply: res }, convKey)
      } catch (e) {
        // Unmounted again mid-resume: leave the persisted ask so the NEXT mount
        // re-attaches; don't write an error.
        if (e instanceof AskCancelledError) return
        // User stopped the resumed ask: rendered by handleStopAsk, not a failure.
        if (e instanceof AskStoppedError) return
        if (e instanceof AskTimeoutError) {
          patchTurn((t) => ({ ...t, timedOut: true, partial: undefined, streamDropped: undefined }))
          return
        }
        const msg = e instanceof Error ? e.message : "Something went wrong"
        patchTurn((t) => ({ ...t, error: msg, streamDropped: undefined }))
        void finalizeConversationTurn(turnId, { error: msg }, convKey)
      } finally {
        askStartRef.current.delete(turnId)
        resumedTurnsRef.current.delete(turnId)
        askingRef.current.delete(convKey)
        busySetRef.current = new Set([...busySetRef.current].filter((k) => k !== convKey))
        setBusy(false)
      }
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hydrating, activeCompany, convKey, finalizeConversationTurn])

  // ── Generate-PRD + the clarify/sufficiency wizard (thin single-conv opener) ─
  const runProjectClarifiedGeneration = useCallback((rawTask: string, sourceDocs: { name: string; content: string }[] | undefined, userMessage: string) => {
    const task = rawTask.length > 4000 ? `${rawTask.slice(0, 3999)}…` : rawTask
    const id = newId()
    const ack: AskResponse = {
      answer: "Generating a PRD for that — it'll open in the panel on the right when ready. Use the View PRD button in this chat to reopen the panel anytime.",
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    setPendingClarify(null)
    setMeta((prev) => ({ ...prev, prdGenerating: true, pendingClarify: undefined }))
    setThread((prev) => [...prev, { id, query: userMessage, reply: ack }])
    setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
    openContentPanel("prd")
    pushPendingConversation(id, userMessage, convKey)
    void finalizeConversationTurn(id, { reply: ack }, convKey)
    void (async () => {
      const onPartial = (html: string) => setContent({ prdPartialHtml: html })
      try {
        const knownConvId = dbConvIdRef.current
        const start = await prdApi.generateFromTask(task, false, sourceDocs, knownConvId)
        if (knownConvId != null) void conversationsApi.update(knownConvId, { prd_id: start.prd_id })
        setMeta((prev) => ({ ...prev, prdId: start.prd_id }))
        const result = await resumePrdGeneration(start.prd_id, undefined, onPartial)
        if (result.ok) {
          setMeta((prev) => ({ ...prev, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false }))
          setContent({ prd: result.prd, prdGenerating: false, prdPartialHtml: null })
        } else {
          setMeta((prev) => ({ ...prev, prdGenerating: false }))
          setContent({ prdGenerating: false, prdPartialHtml: null })
        }
      } catch {
        setMeta((prev) => ({ ...prev, prdGenerating: false }))
        setContent({ prdGenerating: false, prdPartialHtml: null })
      }
    })()
  }, [convKey, setContent, openContentPanel, pushPendingConversation, finalizeConversationTurn])

  // "Generate a PRD for X": run the sufficiency gate first. Not sufficient →
  // seed a clarify-questions turn + park the task; sufficient → generate.
  const runProjectGeneratePrd = useCallback(async (userMessage: string, task: string, sourceDocs: { name: string; content: string }[] | undefined) => {
    let sufficient = true
    let questions: { prompt: string; header?: string | null; options: string[]; skip_default?: string | null }[] = []
    try {
      const res = await prdApi.clarifyTask(task, sourceDocs)
      sufficient = res.sufficient
      questions = res.questions ?? []
    } catch { sufficient = true }
    if (sufficient || questions.length === 0) {
      runProjectClarifiedGeneration(task, sourceDocs, userMessage)
      return
    }
    const turnId = newId()
    const clarify = questions.map((q) => ({ prompt: q.prompt, header: q.header ?? null, options: q.options, skip_default: q.skip_default ?? null }))
    // Match main's clarify turn shape (settleCommandAck): the questions ARE the
    // reply, so the turn carries a real `reply` alongside `.clarify`. Without it
    // `!reply` is true and the shared bubble falls through to its dead-end
    // "No response was generated for this message." note ABOVE the wizard —
    // exactly the state main avoids by settling the reply onto the command turn.
    const reply = {
      answer: clarifyQuestionsText(clarify),
      sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
    } as AskResponse
    setThread((prev) => [...prev, { id: turnId, query: userMessage, reply, clarify }])
    setPendingClarify({ task, sourceDocs, turnId })
    // `meta.pendingClarify` is what the mapped `activeTab.pendingClarify` reads,
    // which drives `clarifyGateOpen` → the inline clarify card renders (main parity).
    setMeta((prev) => ({ ...prev, prdCommandThinking: false, pendingClarify: { task, sourceDocs, turnId } }))
    pushPendingConversation(turnId, userMessage, convKey)
    void finalizeConversationTurn(turnId, { reply }, convKey)
  }, [convKey, pushPendingConversation, runProjectClarifiedGeneration])

  const submitClarifyAnswers = useCallback((answers: ClarifyAnswer[]) => {
    const pc = pendingClarify
    if (!pc) return
    const detail = clarifyAnswersText(answers)
    const combined = detail ? `${pc.task}\n\nAdditional details from the user:\n${detail}` : pc.task
    setThread((prev) => prev.map((t) => t.id === pc.turnId
      ? { ...t, clarifyResolved: { answers: answers.map((a) => ({ prompt: a.prompt, answer: a.answer, assumed: false })), mode: "card" as const } }
      : t))
    runProjectClarifiedGeneration(combined, pc.sourceDocs, detail || "Generate now")
  }, [pendingClarify, runProjectClarifiedGeneration])

  // ── The single-conversation submit (intent dispatch → shared flows/actions) ─
  const submitAsk = useCallback(async (rawQuery: string) => {
    const trimmed = rawQuery.trim()
    if (trimmed.length < 1 && composer.attachments.length === 0) return
    nextPrompts.retire(convKey)
    const askStartedAt = Date.now()
    composer.setPendingSend({ tabId: convKey, query: trimmed, attachments: composer.attachments.map((a) => ({ name: a.name })), startedAt: askStartedAt })
    const settlePendingSend = () => composer.setPendingSend(null)
    const docFile = composer.attachments.find((a) => a.file)?.file ?? null

    // Resolve the durable project conversation BEFORE anything persists or the
    // intent resolver reads conversationId — so the user turn, the ask grounding,
    // the assistant turn, and the suggestions fetch all land on the ONE row that
    // hydrate reads back on reload. On every message after the first this returns
    // synchronously (ref already set); only the first send can await.
    await ensureProjectConv()

    // Clarify-first answers: a parked PRD sufficiency gate — the message IS the
    // answers (or a "generate now" skip), never a fresh command/ask.
    if (pendingClarify && !docFile) {
      const skipped = CLARIFY_SKIP_RE.test(trimmed)
      const combined = skipped ? pendingClarify.task : `${pendingClarify.task}\n\nAdditional details from the user:\n${trimmed}`
      setThread((prev) => prev.map((t) => t.id === pendingClarify.turnId ? { ...t, clarifyResolved: { answers: [], mode: skipped ? "skip" as const : "chat" as const } } : t))
      const sd = pendingClarify.sourceDocs
      setPendingClarify(null)
      runProjectClarifiedGeneration(combined, sd, trimmed)
      settlePendingSend()
      return
    }

    // Attachment early-extraction (so the planner + the ask see the same text).
    let earlyExtracted: (string | null)[] | null = null
    if (composer.attachments.length > 0) {
      earlyExtracted = await Promise.all(composer.attachments.map((a) =>
        a.content ? Promise.resolve<string | null>(a.content)
          : a.file ? askApi.extractFile(a.file).then((r) => r.markdown.slice(0, 50000)).catch(() => null)
          : Promise.resolve<string | null>(a.content ?? null)))
    }
    const sourceDocs = earlyExtracted?.some((t) => t)
      ? composer.attachments.map((a, i) => ({ name: a.name, content: earlyExtracted![i] ?? "" })).filter((d) => d.content)
      : undefined

    if (!trimmed.startsWith("/")) {
      const attachedForIntent = earlyExtracted?.some((t) => t)
        ? composer.attachments.map((a, i) => `--- ${a.name} ---\n${earlyExtracted![i] ?? ""}`).join("\n\n").slice(0, 100000)
        : null
      const intentMessage = attachedForIntent ? `${trimmed}\n\n[Attached files]\n${attachedForIntent}` : trimmed
      // The PRD open beside this chat — main sends its tab's PRD as the planner
      // hint (`tabPrdId`), which the route turns into "Active tab: PRD #X ‹title›
      // is open" so "open/share/edit the PRD" resolves to it. This surface's
      // equivalent is the shared panel's open PRD (`content.prd`); without it a
      // PRD opened via the panel/artifact-list is invisible to the planner
      // (meta.prdId is only set on in-conversation generate/edit), so the same
      // commands fell through to a grounded ask. Workspace-scoped, same as main.
      const openPanelPrdId = content.prd?.prd_id ?? null
      const envelope: ChatIntentEnvelope | null = await chatIntentApi
        .resolve(intentMessage, { conversationId: dbConvIdRef.current, prdId: metaRef.current.prdId ?? openPanelPrdId, hasAttachments: composer.attachments.length > 0 })
        .catch(() => null)
      if (envelope) {
        const targetPrdId = !docFile ? (envelope.prd_id ?? metaRef.current.prdId ?? openPanelPrdId) : null
        const ticketsTarget = !docFile
          ? (metaRef.current.ticketSetId != null ? { ticketSetId: metaRef.current.ticketSetId } as const
            : targetPrdId != null ? { prdId: targetPrdId } as const : null)
          : null
        const result = dispatchChatIntent(
          envelope,
          { hasEditTarget: targetPrdId != null, editTargetPrdId: targetPrdId, ticketsTarget },
          useChatIntentExecutors({
            onGenerateTickets: (env) => {
              if (docFile) {
                // Doc + "make tickets": import the doc as a PRD, then break it
                // into tickets — the shared panel flow, openTickets on (main
                // parity: its onGenerateTickets docFile branch does the same).
                composer.setAttachments([])
                gen.importDocCommandFlow(docFile, {
                  company: activeCompany, openTickets: true, seedQuery: trimmed,
                  artifactTemplateId: env.artifact_template_id,
                })
                settlePendingSend()
                return
              }
              if (metaRef.current.prd) {
                setContent({ prd: metaRef.current.prd, prdMeta: metaRef.current.briefMeta })
                openContentPanel("tickets"); settlePendingSend(); return
              }
              gen.ticketSetCommandFlow(trimmed, env.task?.trim() || trimmed, env.artifact_template_id)
              settlePendingSend()
            },
            onEditPrd: (instruction, prdId) => {
              void runEditPrdAction(instruction, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                contextIds: { prdId },
                onArtifactUpdated: (u) => {
                  if (u.kind === "prd") {
                    setMeta((prev) => ({ ...prev, prd: u.record as unknown as ChatTab["prd"], prdId: u.prdId }))
                    onOpenArtifact?.({ type: "prd", title: "", prd_id: u.prdId } as unknown as OpenArtifactCandidate)
                  }
                },
              })
              settlePendingSend()
            },
            onOpenArtifact: (open) => { gen.openArtifactFlow(trimmed, open); settlePendingSend() },
            onGeneratePrd: (env) => {
              if (docFile) {
                // Doc + "make a PRD": import it via the shared panel flow —
                // streams into the global ContentPanel, same end-result as main's
                // tab-based import (the network + stream primitives are shared).
                composer.setAttachments([])
                gen.importDocCommandFlow(docFile, {
                  company: activeCompany, openTickets: false, seedQuery: trimmed,
                  artifactTemplateId: env.artifact_template_id,
                })
                settlePendingSend()
                return
              }
              void runProjectGeneratePrd(trimmed, env.task ?? trimmed, sourceDocs)
              settlePendingSend()
            },
            onChangeTemplate: (env, prdId) => {
              void gen.prdChangeTemplateFlow(trimmed, convKey, prdId!, env.artifact_template_id!, env.artifact_template_name)
              settlePendingSend()
            },
            onChangeTicketsTemplate: (env, target) => {
              void gen.ticketsChangeTemplateFlow(trimmed, convKey, target, env.artifact_template_id!, env.artifact_template_name)
              settlePendingSend()
            },
            onListArtifacts: (env) => { gen.listArtifactsFlow(trimmed, env); settlePendingSend() },
            onCreateArtifact: (env) => { gen.documentCommandFlow(trimmed, env); settlePendingSend() },
            onShareToSlack: (env) => {
              void runShareToSlackAction(trimmed, env, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                // A REAL `SlackShareTargetRef` (the shipped shape used `{ kind,
                // prdId }`, force-cast — keys the share/preview endpoint ignores,
                // so even the PRD open in the panel never reached a valid target).
                // Mirrors main's `shareRefFor` over this surface's PRD context:
                // the envelope's resolved id, else this conversation's own PRD,
                // else the shared panel's open PRD (`content.prd`). A NAMED subject
                // with no id in context — "share the checkout PRD" — falls through
                // to a title reference the preview resolves workspace-wide,
                // server-side, exactly like main (project-only artifacts stay
                // unresolvable-by-name = the deferred project-context behaviour).
                resolveShareRef: (e): SlackShareTargetRef => {
                  const prdId = e.prd_id ?? metaRef.current.prdId ?? content.prd?.prd_id ?? null
                  const named = (e.artifact_type || "").toLowerCase()
                  if (named === "prd" && prdId) return { prd_id: prdId }
                  // "share this" with a PRD in front → that PRD.
                  if (!e.artifact_query && prdId) return { prd_id: prdId }
                  // A named subject → resolve by title server-side (main parity).
                  if (e.artifact_type || e.artifact_query) {
                    return { artifact_type: e.artifact_type ?? null, artifact_query: e.artifact_query ?? null }
                  }
                  return { prd_id: prdId }
                },
                canAskInDock: true,
                onDockQuestion: (turnId, question) => {
                  if (question.kind !== "slack_channel") return
                  setPendingShare({ turnId, ...question.question })
                },
              })
              settlePendingSend()
            },
            onAssignTickets: (instruction, prdId) => {
              void runAssignTicketsAction(trimmed, instruction, {
                emitTurn,
                runActionTurn: (q, w) => engine.runActionTurnInTab(convKey, q, w),
                contextIds: { prdId },
                canAskInDock: true,
                onDockQuestion: (turnId, question) => {
                  if (question.kind !== "assign") return
                  setPendingAssign({ questions: question.questions, applied: question.applied, turnId })
                },
              })
              settlePendingSend()
            },
            onAnswer: () => {},
          }),
        )
        if (result.handled) return
      }
    }

    // ── Plain grounded ask (fallthrough) ──────────────────────────────────────
    if (askingRef.current.has(convKey)) { settlePendingSend(); return }
    const id = newId()
    const hasAttachments = composer.attachments.length > 0
    const displayQuery = trimmed
    setThread((prev) => [...prev, { id, query: displayQuery, ...(hasAttachments ? { attachments: composer.attachments.map((a) => ({ name: a.name })) } : {}) }])
    settlePendingSend()
    askStartRef.current.set(id, askStartedAt)
    stoppedRef.current = false
    let sendQuery = displayQuery
    let persistedAttachments: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[] | undefined
    if (hasAttachments) {
      setBusy(true)
      try {
        const extracted = await resolveAttachmentRefs(composer.attachments, { preExtracted: earlyExtracted ?? undefined })
        const ctx = extracted.map((e) => `--- ${e.name} ---\n${e.content}`).join("\n\n").slice(0, 100000)
        persistedAttachments = extracted.map((e) => ({ name: e.name, content: e.content, key: e.key, mime: e.mime, size: e.size }))
        setThread((prev) => prev.map((t) => t.id === id ? { ...t, attachments: persistedAttachments } : t))
        sendQuery = `${sendQuery}\n\n[Attached files]\n${ctx}`
        composer.setAttachments([])
      } catch {
        setBusy(false); setThread((prev) => prev.filter((t) => t.id !== id)); return
      }
    }
    await engine.runConversationAsk({ targetTabId: convKey, id, displayQuery, sendQuery, persistedAttachments })
  }, [convKey, composer, engine, nextPrompts, gen, pendingClarify, emitTurn, onOpenArtifact, openContentPanel, setContent, runProjectGeneratePrd, runProjectClarifiedGeneration, ensureProjectConv, activeCompany])

  const handleComposerSubmit = useCallback(() => {
    const q = composer.draft.trim()
    if (q.length < 1 && composer.attachments.length === 0) return
    if (composer.voice.listening) composer.voice.cancel()
    composer.setDraft(""); composer.setPinnedSkill(null); composer.setPlusMenuOpen(false)
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

  // ── Retry a failed/errored ask ─────────────────────────────────────────────
  // The per-turn "Ask again" affordance (mapMainTurns' `onAskAgain`). Same
  // orchestration main runs (`handleAskAgain`): a plain turn re-submits through
  // the shared `submitAsk`; a turn that carried attachments can't be replayed
  // (the files aren't re-uploadable), so it refills the composer for the user.
  const handleAskAgain = useCallback((turn: ThreadTurn) => {
    const q = turn.query.trim()
    if (!q) return
    if (turn.attachments?.length) {
      composer.setDraft(turn.query)
      composer.composerRef.current?.focus()
      return
    }
    void submitAsk(q)
  }, [composer, submitAsk])

  // ── Reopen this conversation's PRD ─────────────────────────────────────────
  // The "View PRD" affordance (ChatArtifactActions, shown once a PRD exists on
  // the turn). Routes through the SAME artifact sink the edit flow + open-
  // candidate cards use (`onOpenArtifact`) — the project surface's single
  // channel into the shared ContentPanel — rather than main's tab-local panel.
  const handleOpenPrd = useCallback(() => {
    const prdId = metaRef.current.prdId
    if (prdId == null) return
    onOpenArtifact?.({ type: "prd", title: "", prd_id: prdId } as unknown as OpenArtifactCandidate)
  }, [onOpenArtifact])

  // ── Open an artifact card from a list-artifacts turn ───────────────────────
  // The click target for `runListArtifactsAction`'s cards (`onOpenArtifactItem`).
  // Mirrors main's `openChatArtifactItem` prd/evidence branch, routed to the
  // project surface's `onOpenArtifact` (whose candidate kind is prd | evidence);
  // main's report/ticket_set/prototype content-panel-resume branches have no
  // project-surface equivalent, so those kinds are left unopened here.
  const openChatArtifactItem = useCallback((a: ChatArtifactItem) => {
    if (a.type !== "prd" && a.type !== "evidence") return
    onOpenArtifact?.({
      type: a.type,
      id: a.id,
      title: a.title,
      status: a.status,
      prd_id: a.open.prd_id ?? null,
      brief_id: a.open.brief_id ?? null,
      insight_index: a.open.insight_index ?? null,
      brief_anchored: a.brief_anchored,
      week_label: a.source.week_label ?? null,
      conversation_id: a.source.conversation_id ?? null,
      conversation_title: a.source.conversation_title || null,
    })
  }, [onOpenArtifact])

  // ── Open the report a chat turn is about, from its title (main parity) ──────
  // Main's `openReportByTitle` verbatim, over THIS conversation's report list:
  // the reply carries no report id (capture runs after the answer, deliberately),
  // so the title is the join key — matched exactly, then leniently (case/space,
  // then either side a prefix of the other). The list comes from the shared
  // `content.threadReports`, which `useThreadReportsSync` fetches for the
  // conversation id we stamp above; guarded by that same stamp so a not-yet-
  // landed (or foreign) list is treated as "no match" rather than the wrong doc.
  const openReportByTitle = useCallback((title: string) => {
    const reports = content.threadReportsConversationId === dbConvIdRef.current
      ? (content.threadReports ?? [])
      : []
    const norm = (s: string) => s.trim().toLowerCase().replace(/\s+/g, " ")
    const want = norm(title)
    const match =
      reports.find((r) => r.title === title) ??
      reports.find((r) => norm(r.title) === want) ??
      reports.find((r) => {
        const have = norm(r.title)
        return have.length > 0 && (have.startsWith(want) || want.startsWith(have))
      })
    if (match) setContent({ reportFocusId: match.id, reportFocusStandalone: false })
    openContentPanel("reports")
  }, [content.threadReports, content.threadReportsConversationId, setContent, openContentPanel])

  // ── Slack share: the interactive card + dock steps (main-parity) ───────────
  // The PREVIEW is seeded by the shared `runShareToSlackAction` (onShareToSlack
  // in submitAsk). These are the post-preview steps — send / re-preview / the
  // channel question — mirrored from main's ChatScreen over this conversation's
  // single thread (main keeps them inline too; no shared extraction exists yet).
  const patchSlackShare = useCallback((
    turnId: string,
    patch: Partial<NonNullable<ThreadTurn["slackShare"]>>,
  ) => {
    setThread((prev) => prev.map((tn) => (tn.id === turnId && tn.slackShare
      ? { ...tn, slackShare: { ...tn.slackShare, ...patch } }
      : tn)))
  }, [])

  // The one place anything is actually posted to Slack (card's Send button).
  const sendSlackShare = useCallback(async (turnId: string, channelId: string, note: string) => {
    const share = threadRef.current.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved || share.busy) return
    const channelName =
      share.preview.channel?.name
      ?? (share.preview.channels ?? []).find((c) => c.id === channelId)?.name
      ?? "the channel"
    patchSlackShare(turnId, { busy: true })
    try {
      await slackShareApi.send(share.ref, channelId, note)
      patchSlackShare(turnId, { busy: false, resolved: { outcome: "sent", channelName } })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Slack rejected the message"
      patchSlackShare(turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [patchSlackShare])

  // The user picked which document from an ambiguous match — re-preview on that
  // one, keeping the channel/note they already had.
  const repreviewSlackShare = useCallback(async (turnId: string, target: SlackShareTarget) => {
    const share = threadRef.current.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved) return
    const ref: SlackShareTargetRef =
      target.type === "prd" ? { prd_id: target.id }
      : target.type === "report" ? { report_id: target.id }
      : target.type === "ticket_set" ? { ticket_set_id: target.id }
      : { custom_artifact_id: target.id }
    patchSlackShare(turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(ref, {
        channel: share.preview.channel?.name ?? share.preview.channel_query ?? null,
      })
      patchSlackShare(turnId, { busy: false, ref, preview })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchSlackShare(turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [patchSlackShare])

  // Card's × / decline — nothing posted; record it so the thread doesn't leave
  // "here's what I'll post" as the last word on a message that never went out.
  const cancelSlackShareCard = useCallback((turnId: string) => {
    const share = threadRef.current.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved) return
    patchSlackShare(turnId, { resolved: { outcome: "cancelled" } })
  }, [patchSlackShare])

  // The dock channel/target question settled — re-preview on the answer (a round
  // trip, both kinds: only the server knows whether the chosen channel is one
  // Sprntly can post to). Mirrors main's `completeShareQuestion`.
  const completeShareQuestion = useCallback(async (_tabId: string, answers: PopupAnswer[]) => {
    const ps = pendingShareRef.current
    if (!ps) return
    setPendingShare(undefined)
    const share = threadRef.current.find((tn) => tn.id === ps.turnId)?.slackShare
    if (!share || share.resolved) return
    const picked = answers.find((a) => !a.skipped)
    if (!picked) { patchSlackShare(ps.turnId, { resolved: { outcome: "cancelled" } }); return }
    const answer = (picked.value ?? picked.answer ?? "").trim()
    if (!answer) { patchSlackShare(ps.turnId, { resolved: { outcome: "cancelled" } }); return }
    if (ps.kind === "target") {
      const target = (share.preview.candidates ?? []).find((c) => `${c.type}-${c.id}` === answer)
      if (!target) return
      await repreviewSlackShare(ps.turnId, target)
      return
    }
    patchSlackShare(ps.turnId, { busy: true })
    try {
      const preview = await slackShareApi.preview(share.ref, { channel: answer.replace(/^#/, "") })
      patchSlackShare(ps.turnId, { busy: false, preview })
      // A typed channel that still doesn't resolve asks again rather than
      // silently dropping the share.
      const next = slackShareQuestionFor(preview)
      if (next) setPendingShare({ turnId: ps.turnId, ...next })
    } catch (e) {
      const msg = e instanceof Error ? e.message : "something went wrong"
      patchSlackShare(ps.turnId, { busy: false, resolved: { outcome: "failed", error: msg } })
    }
  }, [patchSlackShare, repreviewSlackShare])

  // Dock question dismissed → settle the share as NOT sent (main parity).
  const cancelShareQuestion = useCallback((_tabId: string) => {
    const ps = pendingShareRef.current
    setPendingShare(undefined)
    if (ps) patchSlackShare(ps.turnId, { resolved: { outcome: "cancelled" } })
  }, [patchSlackShare])

  // ── Assign: apply the dock popup's ambiguous picks (main-parity) ───────────
  // The batch's ONE landing: the popup collected every pick (finish all
  // questions before anything is sent), and only now do the writes happen, each
  // through the ordinary fields endpoint. Mirrors main's `completeAssign` over
  // the single-conversation thread (no shared extraction exists — main keeps it
  // inline too, `runAssignTicketsAction` only covers the up-front unambiguous
  // apply + raising this question).
  const completeAssign = useCallback(async (_tabId: string, answers: PopupAnswer[]) => {
    const pa = pendingAssignRef.current
    if (!pa) return
    setPendingAssign(undefined)
    setBusy(true)
    const applied = [...pa.applied]
    const failed: string[] = []
    let skipped = 0
    try {
      for (let i = 0; i < pa.questions.length; i++) {
        const q = pa.questions[i]
        const a = answers[i]
        const chosen: TicketAssignQuestion["options"] = []
        if (a && !a.skipped && a.answer) {
          if (q.multi && a.picks?.length) {
            for (const p of a.picks) {
              const opt =
                (p.value != null ? q.options.find((o) => o.value === p.value) : undefined) ??
                q.options.find((o) => o.label === p.label)
              if (opt) chosen.push(opt)
            }
          } else {
            const opt =
              (a.value != null ? q.options.find((o) => o.value === a.value) : undefined) ??
              q.options.find((o) => o.label === a.answer)
            if (opt) chosen.push(opt)
          }
        }
        if (!chosen.length) { skipped += 1; continue }
        for (const opt of chosen) {
          const pair = q.fixed.kind === "ticket"
            ? { key: q.fixed.ticket_key, title: q.fixed.ticket_title, assignee: opt.assignee }
            : { key: opt.value, title: opt.label, assignee: q.fixed.assignee }
          if (!pair.assignee) { skipped += 1; continue }
          try {
            await ticketDataApi.saveFields(pair.key, { assignee: pair.assignee })
            applied.push(`“${pair.title}” → ${pair.assignee.display_name || pair.assignee.email || "them"}`)
          } catch {
            failed.push(pair.title)
          }
        }
      }
      const lines: string[] = []
      if (applied.length) lines.push(`All set — assigned:\n${applied.map((l) => `- ${l}`).join("\n")}`)
      if (skipped) lines.push(`${skipped === 1 ? "One ticket was" : `${skipped} tickets were`} left as they are.`)
      if (failed.length) lines.push(`I couldn't save ${failed.map((t) => `“${t}”`).join(", ")} — try those from the ticket itself.`)
      const summary = !applied.length && !failed.length
        ? "No assignments made — everything was skipped, so the tickets keep their current owners."
        : lines.join("\n\n")
      const reply = {
        answer: summary, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
      } as AskResponse
      const noteId = newId()
      setThread((prev) => [...prev, { id: noteId, query: "", reply }])
      void finalizeConversationTurn(pa.turnId, { reply }, convKey)
    } finally {
      setBusy(false)
    }
  }, [convKey, finalizeConversationTurn])

  // ── Host-bag assembly ──────────────────────────────────────────────────────
  const activeTab = useMemo(() => ({
    id: convKey, hydrating: hydrating && thread.length === 0,
    prdGenerating: !!meta.prdGenerating,
  }), [convKey, hydrating, thread.length, meta.prdGenerating])
  const lastLiveTurnIdx = thread.length - 1
  const composerHintNode = composer.voice.error ? composer.voice.error : null

  // The clarify card's open turn (carries `.clarify` + `.id`), if any.
  const pendingClarifyTurn = useMemo(() => {
    if (!pendingClarify) return null
    return threadRef.current.find((t) => t.id === pendingClarify.turnId) ?? null
  }, [pendingClarify, thread])
  const clarifyPopupOpen = !!pendingClarifyTurn && !clarifyPopupDismissed[pendingClarifyTurn.id]

  const mapDeps: MapMainTurnsDeps = useMemo(() => ({
    animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
    busy,
    activeTab: { id: convKey, prdId: meta.prdId ?? null, prd: meta.prd ?? null, prdGenerating: !!meta.prdGenerating, pendingClarify: meta.pendingClarify },
    name, userInitials, skillForQuery: composer.skillForQuery,
    ticketSetActionState: (meta.ticketSetStatus === "generating" ? "running" : meta.ticketSetStatus === "ready" ? "ready" : meta.ticketSetStatus === "failed" ? "failed" : null),
    showInsightMsg: false, chatEvidenceExists: false,
    chatPrdExists: meta.prdId != null, chatPrdCtaWaiting: false, chatProtoPrdId: null, chatPrototypeReady: false,
    inlinePrdCards: false, inlinePrdAnchorIdx: null, insightCardNode: null, prdQuestionsNode: null,
    clarifyPopupOpen, pendingClarifyTurn,
    handleAskAgain, handleStopAsk: engine.handleStopAsk,
    submitClarifyAnswers, setViewerAttachment,
    openReportByTitle, openArtifactInPanel: (c) => onOpenArtifact?.(c), openChatArtifactItem,
    handleTicketSetAction: gen.handleTicketSetAction, handleOpenEvidence: () => {}, handleOpenPrd,
    handleViewPrototype: () => {}, handlePrototypeSettled: () => {},
    onSendSlackShare: sendSlackShare, onCancelSlackShare: cancelSlackShareCard, onPickSlackShareTarget: repreviewSlackShare,
  }), [lastLiveTurnIdx, busy, convKey, meta, name, userInitials, composer.skillForQuery, engine.handleStopAsk, clarifyPopupOpen, pendingClarifyTurn, submitClarifyAnswers, gen.handleTicketSetAction, onOpenArtifact, handleAskAgain, handleOpenPrd, openChatArtifactItem, openReportByTitle, setViewerAttachment, sendSlackShare, cancelSlackShareCard, repreviewSlackShare])

  const showThreadView = thread.length > 0 || !!activeTab.hydrating || (!!composer.pendingSend && composer.pendingSend.tabId === convKey)

  return {
    thread, mapDeps,
    // The attachment overlay's state, for the host to render the shared viewer.
    viewerAttachment, setViewerAttachment,
    draft: composer.draft, pinnedSkill: composer.pinnedSkill, attachments: composer.attachments,
    composerHintNode,
    plusMenuOpen: composer.plusMenuOpen, plusMenuActive: composer.plusMenuActive,
    slashOpen: composer.slashOpen, filteredSkills: composer.filteredSkills, slashActive: composer.slashActive,
    composerRef: composer.composerRef, fileInputRef: composer.fileInputRef, voice: composer.voice,
    handleSlashSelect: composer.handleSlashSelect, setSlashActive: composer.setSlashActive,
    handleComposerInput: composer.handleComposerInput, handleComposerKeyDown, handleComposerSubmit,
    setPlusMenuActive: composer.setPlusMenuActive, setPlusMenuOpen: composer.setPlusMenuOpen,
    handlePlusMenuSelect: composer.handlePlusMenuSelect, setAttachments: composer.setAttachments,
    setPinnedSkill: composer.setPinnedSkill, handleFileSelect: composer.handleFileSelect,
    handleToggleVoice: composer.handleToggleVoice,
    showChipRow: !showThreadView, displayChips: PROJECT_LANDING_CHIPS, handleHomeCard: () => {},
    handleStarterChip: (text) => { void submitAsk(text) }, showEmptyStarters: false,
    activeTab,
    pendingSendHere: !!composer.pendingSend && composer.pendingSend.tabId === convKey,
    pendingSend: composer.pendingSend,
    pendingClarifyTurn,
    setClarifyPopupDismissed,
    // Assign/share dock popups — parked, rendered, and now APPLIED: completeAssign
    // writes every picked pair (PUT /fields), completeShareQuestion re-previews on
    // the chosen channel/document, both mirroring main over this conversation.
    assignPopupOpen: !!pendingAssign,
    pendingAssignState: pendingAssign,
    activeTabId: convKey,
    completeAssign,
    cancelAssign: () => { setPendingAssign(undefined) },
    sharePopupOpen: !!pendingShare,
    pendingShareState: pendingShare,
    completeShareQuestion,
    cancelShareQuestion,
    setQuestionDockEl,
    nextPrompts, submitAsk, showThreadView,
    threadScrollRef: scroll.threadScrollRef, handleThreadScroll: scroll.handleThreadScroll, setThreadContentEl: scroll.setThreadContentEl,
  }
}
