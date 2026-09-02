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

import { createElement, Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react"
// Single source of truth for the busy-send hint copy — main defines it beside
// its own composer. Reused (not duplicated) so the two surfaces stay verbatim.
import { BUSY_ENTER_HINT_LEAD, BUSY_ENTER_HINT_TAIL } from "../ChatScreen"
import { profileDisplayName, useWorkspace } from "../../../../context/WorkspaceContext"
import { useCompany } from "../../../../context/CompanyContext"
import { useContent } from "../../../../context/ContentContext"
import { useNavigation } from "../../../../context/NavigationContext"
import { createChatPersistence, replyToText } from "../../../../lib/chatPersistence"
import {
  conversationsApi, prdApi, chatIntentApi, askApi, chatSuggestionsApi, projectsApi,
  type AskResponse, type ChatIntentEnvelope, type OpenArtifactCandidate, type TicketAssignQuestion,
  type ChatArtifactItem, type SlackShareTargetRef,
} from "../../../../lib/api"
import { type PopupAnswer } from "../../../shared/QuestionPopup"
import { DRAFT_MIN_CHARS } from "../../../shared/ChatComposer"
import { buildQuotedMessage, splitQuotedSuffix } from "../../../../lib/chatQuote"
import {
  useSlackShareCardHandlers,
  type PendingShareState,
} from "../../../shared/chat-shell/conversation/useSlackShareCardHandlers"
import { useAssignCompletion } from "../../../shared/chat-shell/conversation/useAssignCompletion"
import { askAgain } from "../../../shared/chat-shell/conversation/askAgain"
import { runClarifiedGeneration } from "../../../shared/chat-shell/conversation/clarifiedGeneration"
import { getPendingAsk, resumeAskGeneration, AskCancelledError, AskStoppedError, AskTimeoutError } from "../../../../lib/runAskGeneration"
import { GROUNDED_PROGRESS_ENABLED } from "../../../../lib/friendlyPhase"
import { resolveAttachmentRefs } from "../../../shared/chatComposerController"
import { dispatchChatIntent } from "../../../../lib/chat/dispatchChatIntent"
import { useChatIntentExecutors } from "../../../shared/chat-shell/useChatIntentExecutors"
import { runEditPrdAction, runShareToSlackAction, runAssignTicketsAction } from "../../../shared/chat-shell/conversation/actions"
import { resolveShareRef } from "../../../shared/chat-shell/conversation/resolveShareRef"
import { useDocumentReopenProbe } from "../../../shared/chat-shell/conversation/useDocumentReopenProbe"
import { matchReportByTitle } from "../../../shared/chat-shell/conversation/matchReportByTitle"
import { useNextPrompts, type NextPromptsAdapter } from "../../../shared/chat-shell/useNextPrompts"
import { DEFAULT_HOME_CHIPS } from "../../../../lib/homeChips"
import { type ClarifyAnswer, clarifyQuestionsText } from "../../../shared/ClarifyQuestionsCard"
import { useComposer } from "../useComposer"
import { GreetingTurnBody } from "./GreetingTurnBody"
import { useThreadScroll } from "../useThreadScroll"
import { useMainConversation } from "../useMainConversation"
import { useConversationGeneration } from "../useConversationGeneration"
import { useRealtimeChannel } from "./useRealtimeChannel"
import type { ConversationHandle, AskGrounding } from "../conversationCore"
import type { ThreadTurn, ChatTab } from "../ChatScreen"
import type { ConversationViewProps } from "../ConversationView"
import type { MapMainTurnsDeps } from "../../../shared/chat-shell/types"
import { MORE_MARKER } from "../../../shared/chat-shell/types"

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

// How long a turn's transient "Copied" tick shows after a per-turn Copy. Same
// value main's ChatScreen keeps for the identical affordance (kept a local
// const here because main's is module-private).
const COPIED_HINT_MS = 1600

function surfaceKey(projectId: number | string): string {
  return `project-${projectId}-individual`
}

// The project chat's empty-state landing chips: main's shared default set minus
// the "home" brief chip (its handler navigates to workspace-only screens, which
// have no project-surface equivalent). Each starter chip submits its prompt
// through the adapter's `submitAsk` via `handleStarterChip`.
const PROJECT_LANDING_CHIPS = DEFAULT_HOME_CHIPS.filter((c) => c.kind === "starter")

const newId = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `turn-${Date.now()}`)

/** The whitelisted DTO shape the server publishes on `turn.created` /
 *  `brief.delivered` (mirrors `IndividualTurn` / `_TURN_CREATED_DTO_KEYS`,
 *  backend/app/routes/projects.py). */
export type RealtimeTurnPayload = {
  id: number
  role: "user" | "assistant"
  content: string
  created_at?: string
}

/** Narrow an unknown broadcast payload to `RealtimeTurnPayload`, or `null`
 *  when it doesn't carry the fields this surface renders on — a malformed/
 *  future-shaped payload is dropped rather than crashing the append.
 *  Exported (alongside the two helpers below) for direct unit-testing of the
 *  dedupe contract, independent of the DOM/React harness. */
export function parseRealtimeTurnPayload(payload: unknown): RealtimeTurnPayload | null {
  const p = payload as Partial<RealtimeTurnPayload> | null | undefined
  if (!p || typeof p.id !== "number" || typeof p.content !== "string") return null
  if (p.role !== "user" && p.role !== "assistant") return null
  // Blank content never renders anything real — mirrors hydrate's own
  // `content.trim()` guard on a standalone assistant row (below) — and a
  // blank body is exactly what an empty-tool-loop / interrupted write can
  // persist server-side. Dropping it here, at the parse boundary, keeps
  // both `shouldAppendRealtimeTurn` and the merge/append logic below from
  // ever having to reason about an empty-string turn at all.
  if (p.content.trim() === "") return null
  return { id: p.id, role: p.role, content: p.content, created_at: p.created_at }
}

/** Whether an incoming realtime turn is safe to apply to `existing` at all
 *  (id-dedupe + no double-render on a local-echo race). False in three
 *  cases:
 *   - the exact DB row is ALREADY on the thread (a rehydrated turn, or a
 *     prior delivery of the SAME broadcast/reconcile) — matched by
 *     `dbTurnId`, the durable row id every rendered turn eventually carries.
 *   - the client's OWN optimistic echo of this same turn is still showing,
 *     not yet reconciled with its row id (`dbTurnId == null`) — matched by
 *     role + exact content over a short recent window (broadcasts are
 *     at-most-once and typically land within moments of the local send, so
 *     only the tail of the thread is worth checking; a genuine repeat
 *     message further back in history is never suppressed).
 *   - this exact assistant row was already MERGED into a paired user turn
 *     by a previous call (`mergedReplyIds`, optional — passed only by
 *     `applyRealtimeTurn`'s caller below). A merge sets the paired turn's
 *     `reply` but deliberately leaves `dbTurnId` pointing at the USER row
 *     (rewind needs the user row's id, not the assistant's — see
 *     `realtimeTurnToThreadTurn`), so the plain `dbTurnId` check above
 *     cannot by itself catch a redelivery of that same assistant row; this
 *     is the second, independent guard that closes it. */
export function shouldAppendRealtimeTurn(
  existing: ThreadTurn[],
  payload: RealtimeTurnPayload,
  mergedReplyIds?: ReadonlySet<number>,
): boolean {
  if (existing.some((t) => t.dbTurnId === payload.id)) return false
  if (mergedReplyIds?.has(payload.id)) return false
  const recentUnreconciled = existing.slice(-6).filter((t) => t.dbTurnId == null)
  if (payload.role === "user") {
    return !recentUnreconciled.some((t) => t.query === payload.content)
  }
  return !recentUnreconciled.some((t) => t.reply?.answer === payload.content)
}

/** Shape a whitelisted realtime DTO into the same bare, query-or-reply-only
 *  `ThreadTurn` the hydrate restore already builds for a standalone
 *  user/assistant row (see the hydrate effect below) — used when
 *  `applyRealtimeTurn` has nothing to pair an assistant turn with (or for a
 *  user turn.created, which always arrives before its reply exists). */
export function realtimeTurnToThreadTurn(payload: RealtimeTurnPayload): ThreadTurn {
  if (payload.role === "user") {
    return { id: newId(), dbTurnId: payload.id, query: payload.content }
  }
  return {
    id: newId(), dbTurnId: payload.id, query: "",
    reply: {
      answer: payload.content, sources: [], follow_ups: [], key_points: [],
      citations: [], confidence: 1, unanswered: "",
    } as AskResponse,
  }
}

/** Apply one incoming realtime turn to `existing`, mirroring hydrate's own
 *  query+reply PAIRING (~hydrate effect below: a `user` row is paired with
 *  the row immediately after it) instead of appending every `turn.created`
 *  event as its own separate `ThreadTurn`. Previously every assistant
 *  turn.created became a headless `{query:"", reply}` turn beside a
 *  reply-less `{query}` turn from the user's own turn.created — on an
 *  observer tab (no optimistic echo) the reply-less user turn rendered the
 *  "No response was generated for this message." fallback
 *  (`ChatBubble.tsx`'s no-reply ladder) directly above its own real answer.
 *
 *  Gated first by `shouldAppendRealtimeTurn` (id/local-echo dedupe,
 *  unchanged) — a duplicate or optimistic-echo delivery is dropped exactly
 *  as before. An assistant turn.created that passes the gate MERGES into
 *  the thread's LAST turn when that turn is a reply-less user turn (the
 *  adjacent pairing hydrate itself relies on: the assistant row is always
 *  written immediately after its user row) — setting `reply` in place
 *  rather than appending a second, headless turn. It only appends a
 *  standalone assistant turn when the last turn is NOT an unpaired user
 *  turn — e.g. a `brief.delivered` notice, which has no matching ask in
 *  this thread at all. A user turn.created always appends bare: its pairing
 *  partner (the reply) hasn't arrived yet. */
export function applyRealtimeTurn(
  existing: ThreadTurn[],
  payload: RealtimeTurnPayload,
  mergedReplyIds?: ReadonlySet<number>,
): ThreadTurn[] {
  if (!shouldAppendRealtimeTurn(existing, payload, mergedReplyIds)) return existing
  const last = existing[existing.length - 1]
  if (payload.role === "assistant" && last && last.query && !last.reply) {
    const shaped = realtimeTurnToThreadTurn(payload)
    return [...existing.slice(0, -1), { ...last, reply: shaped.reply }]
  }
  return [...existing, realtimeTurnToThreadTurn(payload)]
}

export function useProjectConversation(
  projectId: number | string,
  /** The caller's own uid — needed to subscribe to this chat's PER-USER
   *  realtime topic (`project:{id}:user:{uid}`). `null`/omitted (unresolved
   *  auth) leaves this surface realtime-blind, same as before this ticket:
   *  no crash, just no live turn.created/brief.delivered updates. */
  currentUserId?: string | null,
  onOpenArtifact?: (candidate: OpenArtifactCandidate) => void,
): ProjectConversationProps {
  const convKey = useMemo(() => surfaceKey(projectId), [projectId])
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
  // Acting on a past prompt (main parity): the ONE turn currently open in the
  // inline editor, and the ONE showing its transient "Copied" tick. Host-owned,
  // exactly like main's ChatScreen keeps them.
  const [editingTurnId, setEditingTurnId] = useState<string | null>(null)
  const [copiedTurnId, setCopiedTurnId] = useState<string | null>(null)
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
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
  // The highest `conversation_turns.id` this surface has SEEN (hydrated,
  // realtime-delivered, or reconciled) — the since-cursor the realtime
  // reconcile reads after every (re)subscribe (AD-P22). Seeded from
  // hydrate's own restored turns below.
  const lastKnownTurnIdRef = useRef<number>(0)
  // Every assistant DB row id whose reply is ALREADY present on the thread but
  // whose `dbTurnId` slot is NOT the assistant's own — either merged into a
  // paired user turn by a live realtime event (`applyIncomingTurn` below) or
  // folded into a paired turn by the hydrate restore (which keeps the USER
  // row's id on the merged turn, for rewind). A live re-broadcast of such a
  // row bypasses the reconcile since-cursor, so the plain `dbTurnId` dedupe
  // can't catch it; `shouldAppendRealtimeTurn` consults this set as the
  // second, id-precise guard. Seeded by hydrate (paired assistant rows) and
  // grown by `applyIncomingTurn` (realtime merges).
  const mergedReplyIdsRef = useRef<Set<number>>(new Set())
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
        const conv = await import("../../../../lib/api").then((m) => m.projectsApi.individualChat(projectId))
        if (cancelled) return
        setDbConvId(conv.id)
        const { turns } = await conversationsApi.listTurns(conv.id)
        if (cancelled) return
        const restored: ThreadTurn[] = []
        // Assistant DB row ids that hydrate FOLDED into a paired user turn: the
        // merged turn keeps the USER row's id on `dbTurnId` (rewind needs it),
        // so the assistant row's own id lives nowhere on the thread. Recorded
        // into `mergedReplyIdsRef` below so a realtime re-broadcast of that same
        // assistant row is deduped by id instead of re-rendered as a duplicate.
        const pairedReplyIds: number[] = []
        for (let i = 0; i < (turns ?? []).length; i++) {
          const t = turns[i]
          if (t.role === "user") {
            const next = turns[i + 1]
            const reply = next?.role === "assistant"
              ? { answer: next.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse
              : undefined
            restored.push({
              id: `resumed-${conv.id}-${i}`,
              // Carry the DB row id so a rehydrated turn can still be rewound to
              // (edit/retry a past prompt): the server needs the real row id,
              // and the persistence map only covers turns THIS session wrote.
              // Mirrors main's `buildRestored` dbTurnId threading.
              ...(typeof t.id === "number" ? { dbTurnId: t.id } : {}),
              query: t.content,
              reply,
            })
            if (reply) {
              if (typeof next!.id === "number") pairedReplyIds.push(next!.id)
              i++
            }
          } else if (t.role === "assistant" && t.content.trim()) {
            restored.push({
              id: `resumed-${conv.id}-${i}`,
              // A standalone assistant row (a delegated brief/notice with no
              // adjacent user ask) DOES carry its own id here — nothing else
              // holds it — so a realtime re-broadcast of that exact row is
              // caught by the plain `dbTurnId` dedupe rather than appended twice.
              ...(typeof t.id === "number" ? { dbTurnId: t.id } : {}),
              query: "",
              reply: { answer: t.content, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } as AskResponse,
            })
          }
        }
        restored.forEach((r) => resumedTurnsRef.current.add(r.id))
        // Seed the realtime reconcile's since-cursor PAST EVERY row hydrate just
        // read — the first reconcile (fired the instant the channel below
        // subscribes) then only fetches genuinely NEW turns. Seeding from the
        // restored turns' `dbTurnId`s alone left the cursor at the last USER
        // row's id: a paired assistant row (written right after its user row,
        // so a HIGHER id) sits above that cursor but off the thread's dbTurnIds,
        // so the reconcile refetched it and re-appended it as a duplicate
        // standalone bubble. Reading the raw `turns` ids (which include the
        // paired assistant rows) closes that gap.
        for (const t of (turns ?? [])) {
          if (typeof t.id === "number") lastKnownTurnIdRef.current = Math.max(lastKnownTurnIdRef.current, t.id)
        }
        // A live re-broadcast bypasses the since-cursor, so also remember every
        // paired assistant row id for `shouldAppendRealtimeTurn`'s id-precise
        // second guard (the merged turn's `dbTurnId` is the user row's, not the
        // assistant's, so the plain dbTurnId dedupe can't catch these on its own).
        for (const id of pairedReplyIds) mergedReplyIdsRef.current.add(id)
        // Only fill a still-empty thread — never clobber one a send already started.
        if (restored.length) setThread((prev) => (prev.length === 0 ? restored : prev))
      } catch {
        /* leave empty */
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // ── Realtime: this chat's own per-user topic ────────────────────────────────
  // `turn.created` (a fresh individual-chat write — the generate/clarify/
  // terminal-outcome branches and the PRD-edit path) and `brief.delivered` (a
  // delegated brief/notice; the server already publishes it) both apply live
  // via `applyRealtimeTurn` — id/local-echo deduped exactly as before, but
  // PAIRED the way hydrate pairs a restored user+assistant row (see that
  // function's own doc). `onReconcile` (fired once on every (re)subscribe,
  // AD-P22) refetches anything landed since `lastKnownTurnIdRef` — the gap a
  // dropped broadcast or a reconnect would otherwise leave.
  //
  // `mergedReplyIdsRef` (declared above, beside `lastKnownTurnIdRef`, so the
  // hydrate effect can seed it too): an assistant turn.created that MERGES into
  // a paired user turn deliberately never gets its own `dbTurnId` slot (the
  // merged turn keeps the user row's id — see `applyRealtimeTurn`), so a later
  // redelivery of that exact assistant row wouldn't be caught by the
  // dbTurnId-dedupe alone. The ref remembers every payload id that WAS merged
  // (never a standalone-appended one, which is already covered by its own
  // `dbTurnId`) so `shouldAppendRealtimeTurn` can still catch it.
  const applyIncomingTurn = useCallback((payload: RealtimeTurnPayload) => {
    lastKnownTurnIdRef.current = Math.max(lastKnownTurnIdRef.current, payload.id)
    setThread((prev) => {
      const next = applyRealtimeTurn(prev, payload, mergedReplyIdsRef.current)
      // A merge replaces the thread with a same-LENGTH array (one turn's
      // `reply` set in place); an append or a no-op (deduped) never does —
      // append grows the array, and a no-op returns the identical `prev`
      // reference. Length-equality plus a changed reference is therefore an
      // unambiguous "this call merged" signal, without `applyRealtimeTurn`
      // needing to hand back anything beyond the new array.
      if (next !== prev && next.length === prev.length) mergedReplyIdsRef.current.add(payload.id)
      return next
    })
  }, [])

  const handleRealtimeEvent = useCallback((event: string, payload: unknown) => {
    if (event !== "turn.created" && event !== "brief.delivered") return
    const parsed = parseRealtimeTurnPayload(payload)
    if (parsed) applyIncomingTurn(parsed)
  }, [applyIncomingTurn])

  const handleRealtimeReconcile = useCallback(() => {
    void projectsApi.individualTurns(projectId, lastKnownTurnIdRef.current)
      .then((turns) => {
        for (const t of turns) applyIncomingTurn({ id: t.id, role: t.role, content: t.content, created_at: t.created_at })
      })
      .catch(() => { /* best-effort — the NEXT reconnect/reconcile closes it */ })
  }, [projectId, applyIncomingTurn])

  // Gated on `!hydrating`: the channel's first reconcile fires the instant it
  // subscribes, and a reconcile-driven append landing BEFORE hydrate's own
  // `setThread` would make hydrate's `prev.length === 0 ? restored : prev`
  // guard a silent no-op (losing the ordered, paired restore for the bare
  // reconcile shape). Waiting for hydrate to settle first — regardless of
  // whether it restored anything — makes the ordering safe either way.
  const conversationRealtimeTopic = !hydrating && currentUserId
    ? `project:${projectId}:user:${currentUserId}`
    : null
  useRealtimeChannel(conversationRealtimeTopic, {
    onEvent: handleRealtimeEvent,
    onReconcile: handleRealtimeReconcile,
  })

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
  // This surface's context handed to the SHARED probe: its per-conversation
  // guards + once-per-conversation marker (`begin`), its `dbConvIdRef`/
  // `contentPanelTab`/`content.documentId` post-fetch reads, and NO late-
  // precedence arm (main's "tickets win" has no project counterpart — a
  // ticket-set that owns the panel already bails in `begin`). Deps unchanged.
  useDocumentReopenProbe(
    {
      begin: () => {
        if (hydrating || dbConvId == null) return null
        const convId = dbConvId
        if (documentProbedRef.current.has(convId)) return null
        const m = metaRef.current
        if (m.prd || m.prdGenerating || m.prdId != null) return null
        if (m.ticketSetId != null) return null
        documentProbedRef.current.add(convId)
        return convId
      },
      stillActive: () => dbConvIdRef.current === dbConvId,
      panelOpen: () => Boolean(contentPanelTab),
      documentClaimed: () => content.documentId != null,
      setContent,
      openContentPanel,
    },
    [hydrating, dbConvId, contentPanelTab, content.documentId, setContent, openContentPanel],
  )

  // ── Persistence via conversation_turns (server-only writes) ────────────────
  const persistence = useMemo(() => {
    return createChatPersistence({
      getApi: () => import("../../../../lib/api").then((m) => m.conversationsApi),
      getTabConvId: () => dbConvIdRef.current,
      getTabPrdId: () => metaRef.current.prdId ?? null,
      setTabConvId: (_key, convId) => { setDbConvId(convId) },
      onConversationCreated: () => { /* no rail entry on a project surface */ },
      // Route the create path to the DURABLE project conversation. The shared gen
      // flows (create-artifact/import-PRD/ticket-set) call `ensureConversation`
      // when a tab has no bound row; the generic path mints a THROWAWAY
      // `conversationsApi.create` row, which the reload-time hydrate — reading the
      // project row — can't see, so a chat-written document orphans from its
      // thread (the same fork the top-of-file note calls out for sends). Delegate
      // to `ensureProjectConv` (get-or-create per project+surface) so every such
      // artifact attaches to the one row hydrate reads back.
      resolveConversationId: () =>
        (ensureProjectConvRef.current ? ensureProjectConvRef.current() : Promise.resolve(null)),
    })
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
      const conv = await projectsApi.individualChat(projectId)
      dbConvIdRef.current = conv.id
      setDbConvId(conv.id)
      return conv.id
    } catch {
      return null
    }
  }, [projectId])
  // Publish for the persistence create-path override (declared above the memo).
  ensureProjectConvRef.current = ensureProjectConv

  // conversation_id, NO project_id (main chat on a project-bound row).
  const resolveAskParams = useCallback(async (
    _key: string, _m: { turnId: string; displayQuery: string },
  ): Promise<{ convId: number | null; grounding: AskGrounding }> => {
    const convId = dbConvIdRef.current ?? await ensureProjectConv()
    // Pin this project's context source on every send: the backend routes it to
    // `ProjectContextAssembler` (membership-gated server-side), which folds the
    // project's roster/ledger/artifacts/memory into the answer. `surface` is
    // always "individual" now (the group mount was removed) — the backend's
    // `context_source.params` shape is unchanged so this ticket touches no
    // backend contract. conversation_id still rides for history replay + the
    // conv↔project bind.
    const context_source = {
      kind: "project",
      params: { project_id: projectId, surface: "individual" as const },
    }
    const grounding: AskGrounding = convId != null
      ? { conversation_id: convId, context_source }
      : { context_source }
    return { convId: convId ?? null, grounding }
  }, [ensureProjectConv, projectId])

  const pushPendingConversation = useCallback((
    turnId: string, query: string, key: string,
    attachments?: { name: string; content: string; key?: string | null; mime?: string | null; size?: number | null }[],
  ) => {
    const title = query.length > 52 ? `${query.slice(0, 49)}…` : query
    void persistence.pushUserTurn(key, { turnId, title, query, attachments })
  }, [persistence])

  const finalizeConversationTurn = useCallback((
    turnId: string,
    updates: { reply?: AskResponse; error?: string; clientMessageId?: string },
    key: string,
  ): Promise<void> => {
    // `clientMessageId` (an ask's reply-persist dedup key, when the caller
    // has one — see `runAskGeneration`'s `replyClientMessageId` doc)
    // threads straight to the write: THIS surface is exactly the one
    // `client_message_id` exists for. Its ask-scope (`askScope(convKey)`,
    // `surfaceKey(projectId)`) is the SAME across every tab/mount on this
    // project — unlike main chat's per-tab uuid scope — so a second
    // mount/tab can independently resume and persist the SAME completed ask.
    // Stamping the identical key on both persists lets the server's
    // idempotent upsert collapse a same-key double-submit to one row.
    if (updates.reply) {
      return persistence.pushAssistantTurn(
        key, replyToText(updates.reply), undefined, updates.clientMessageId,
      )
    }
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
    activeCompany,
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
    // Captured HERE, before this resume's own poll runs — not re-read once it
    // settles. This conversation's ask-scope (`askScope(convKey)`) is the
    // SAME across every tab/mount on this project, so the ORIGINATING send
    // may have minted a reply dedup key under this SAME pending-job record
    // (`runAskGeneration`'s `replyClientMessageId`); by the time this poll
    // resolves it has already cleared that record itself
    // (`_pollAskLoop`'s clear-on-terminal-exit), so the key must travel with
    // this closure, exactly like `askId`/`turnId` already do.
    const replyClientMessageId = pending.clientMessageId
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
          // Grounded progress on a re-attached private-project generation — same
          // curated, flag-gated `livePhase` seam the shared POST engine uses.
          GROUNDED_PROGRESS_ENABLED
            ? (label) => patchTurn((t) => (!t.reply && !t.stopped ? { ...t, livePhase: label } : t))
            : undefined,
        )
        // If it streamed, mark animated BEFORE the reply lands so the typewriter
        // doesn't re-reveal text already read (main's `hasFreshReply` reasoning).
        const streamed = threadRef.current.find((t) => t.id === turnId)
        if (streamed?.partial) animatedTurnIds.current.add(turnId)
        patchTurn((t) => ({ ...t, reply: res, partial: undefined, streamDropped: undefined, timedOut: undefined, livePhase: undefined }))
        void finalizeConversationTurn(turnId, { reply: res, clientMessageId: replyClientMessageId }, convKey)
      } catch (e) {
        // Unmounted again mid-resume: leave the persisted ask so the NEXT mount
        // re-attaches; don't write an error.
        if (e instanceof AskCancelledError) return
        // User stopped the resumed ask: rendered by handleStopAsk, not a failure.
        if (e instanceof AskStoppedError) return
        if (e instanceof AskTimeoutError) {
          patchTurn((t) => ({ ...t, timedOut: true, partial: undefined, streamDropped: undefined, livePhase: undefined }))
          return
        }
        const msg = e instanceof Error ? e.message : "Something went wrong"
        patchTurn((t) => ({ ...t, error: msg, streamDropped: undefined, livePhase: undefined }))
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
  // The trim + ack + synchronous-seed + async generate→bind→resume→dispatch
  // sequence lives in the shared `clarifiedGeneration` unit so it can't drift
  // from the tab-scoped surface (ChatScreen). This surface injects its
  // single-conversation seams (setMeta/setThread instead of setTabs, no toast /
  // summary / active-tab guard).
  const runProjectClarifiedGeneration = useCallback((rawTask: string, sourceDocs: { name: string; content: string }[] | undefined, userMessage: string) => {
    runClarifiedGeneration(rawTask, sourceDocs, userMessage, {
      newId,
      seedAckTurn: (id, message, ack) => {
        setPendingClarify(null)
        setMeta((prev) => ({ ...prev, prdGenerating: true, pendingClarify: undefined }))
        setThread((prev) => [...prev, { id, query: message, reply: ack }])
      },
      openPanel: () => {
        setContent({ prd: null, prdGenerating: true, prdPartialHtml: null })
        openContentPanel("prd")
      },
      pushPendingConversation: (id, message) => pushPendingConversation(id, message, convKey),
      finalizeAck: (id, ack) => { void finalizeConversationTurn(id, { reply: ack }, convKey) },
      onPartial: (html) => setContent({ prdPartialHtml: html }),
      resolveKnownConvId: () => dbConvIdRef.current,
      generateFromTask: (task, docs, knownConvId) => prdApi.generateFromTask(task, false, docs, knownConvId),
      onStarted: (start, knownConvId) => {
        if (knownConvId != null) void conversationsApi.update(knownConvId, { prd_id: start.prd_id })
        setMeta((prev) => ({ ...prev, prdId: start.prd_id }))
      },
      onSuccess: (_start, result) => {
        setMeta((prev) => ({ ...prev, prd: result.prd, prdId: result.prd.prd_id, prdGenerating: false }))
        setContent({ prd: result.prd, prdGenerating: false, prdPartialHtml: null })
      },
      onFailure: () => {
        setMeta((prev) => ({ ...prev, prdGenerating: false }))
        setContent({ prdGenerating: false, prdPartialHtml: null })
      },
      onError: () => {
        setMeta((prev) => ({ ...prev, prdGenerating: false }))
        setContent({ prdGenerating: false, prdPartialHtml: null })
      },
    })
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
      const commandText = trimmed
      const attachedForIntent = earlyExtracted?.some((t) => t)
        ? composer.attachments.map((a, i) => `--- ${a.name} ---\n${earlyExtracted![i] ?? ""}`).join("\n\n").slice(0, 100000)
        : null
      const intentMessage = attachedForIntent ? `${commandText}\n\n[Attached files]\n${attachedForIntent}` : commandText
      // The PRD open beside this chat — main sends its tab's PRD as the planner
      // hint (`tabPrdId`), which the route turns into "Active tab: PRD #X ‹title›
      // is open" so "open/share/edit the PRD" resolves to it. This surface's
      // equivalent is the shared panel's open PRD (`content.prd`); without it a
      // PRD opened via the panel/artifact-list is invisible to the planner
      // (meta.prdId is only set on in-conversation generate/edit), so the same
      // commands fell through to a grounded ask. Workspace-scoped, same as main.
      const openPanelPrdId = content.prd?.prd_id ?? null
      // Pin THIS project's context source on the intent resolve, exactly as
      // `resolveAskParams` does for the ask path. Without it the backend's
      // `list_artifacts`/`open_artifact` legs default to the workspace-wide
      // listing, so "which PRDs are in this project?" returned the whole
      // workspace's newest artifacts (matching main's parity default) rather
      // than this project's — the same project_id the ask path already sends
      // makes the intent envelope's cards agree with the project-scoped prose.
      const envelope: ChatIntentEnvelope | null = await chatIntentApi
        .resolve(intentMessage, {
          conversationId: dbConvIdRef.current,
          // Open-panel PRD wins over the generation-time `metaRef` cache. On a
          // long multi-PRD thread `metaRef.current.prdId` holds an EARLIER PRD,
          // so "edit/open/share the PRD" must resolve to the one actually in
          // front of the user (the shared panel's `content.prd`); `metaRef` is
          // the fallback for the brief window right after a fresh generate,
          // before the panel catches up. Mirrors main's `tabPrdId`
          // (`prd?.prd_id ?? prdId`) and the Slack `resolveShareRef` precedence.
          prdId: openPanelPrdId ?? metaRef.current.prdId ?? null,
          hasAttachments: composer.attachments.length > 0,
          contextSource: { kind: "project", params: { project_id: projectId, surface: "individual" as const } },
        })
        .catch(() => null)
      if (envelope) {
        // The planner's explicit `prd_id` wins (it resolved a named subject),
        // then the OPEN-PANEL PRD, then the stale `metaRef` cache as a last
        // resort — same open-panel-first precedence as the intent resolve above,
        // so edit / assign-tickets / change-template all target the PRD the user
        // is looking at rather than an earlier one from this thread.
        const targetPrdId = !docFile ? (envelope.prd_id ?? openPanelPrdId ?? metaRef.current.prdId ?? null) : null
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
              gen.ticketSetCommandFlow(trimmed, env.task?.trim() || commandText, env.artifact_template_id)
              settlePendingSend()
            },
            onEditPrd: (instruction, prdId) => {
              void runEditPrdAction(trimmed, instruction, {
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
              void runProjectGeneratePrd(trimmed, env.task ?? commandText, sourceDocs)
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
                // A REAL `SlackShareTargetRef` via the SAME shared resolver main
                // uses (the shipped inline shape used `{ kind, prdId }`, force-cast
                // — keys the share/preview endpoint ignores, so even the PRD open
                // in the panel never reached a valid target; the inline copy also
                // dropped the ticket-set and report arms). Build this surface's
                // context — this conversation's own PRD (else the shared panel's
                // open PRD, `content.prd`), its ticket set, and the panel's focused
                // report — and hand it to the resolver, which applies the same
                // precedence on every surface. A NAMED subject with no id in
                // context — "share the checkout PRD" — falls through to a title
                // reference the preview resolves workspace-wide, server-side,
                // exactly like main (project-only artifacts stay unresolvable-by-
                // name = the deferred project-context behaviour).
                resolveShareRef: (e): SlackShareTargetRef => resolveShareRef(e, {
                  // Open-panel PRD first, stale `metaRef` cache as fallback — the
                  // share context is "the document in front of the user", so on a
                  // multi-PRD thread this must be the panel's open PRD, not an
                  // earlier one cached at generation time. Matches the edit target
                  // above and the resolver's own documented precedence.
                  prdId: content.prd?.prd_id ?? metaRef.current.prdId ?? null,
                  ticketSetId: metaRef.current.ticketSetId ?? null,
                  reportId: content.reportFocusId ?? null,
                }),
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
    // Enter while this conversation's answer is still streaming: show the busy
    // hint and DON'T drop the message — mirrors main's `handleComposerSubmit`
    // busy branch. This must run BEFORE the draft is cleared below; otherwise
    // the submitAsk-internal asking-guard fires after `setDraft("")` and
    // settlePendingSend, so the text and the optimistic bubble vanish with zero
    // feedback (the network guard alone prevents the duplicate ask but says
    // nothing to the user).
    if (askingRef.current.has(convKey)) { composer.showComposerHint("busy"); return }
    if (composer.voice.listening) composer.voice.cancel()
    composer.setDraft(""); composer.setPinnedSkill(null); composer.setPlusMenuOpen(false)
    void submitAsk(q)
  }, [composer, submitAsk, convKey])
  const handleComposerKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (composer.slashOpen) {
      if (e.key === "ArrowDown") { e.preventDefault(); composer.setSlashActive((i) => (i + 1) % composer.filteredSkills.length); return }
      if (e.key === "ArrowUp") { e.preventDefault(); composer.setSlashActive((i) => (i - 1 + composer.filteredSkills.length) % composer.filteredSkills.length); return }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); composer.handleSlashSelect(composer.filteredSkills[composer.slashActive] ?? composer.filteredSkills[0]); return }
      if (e.key === "Escape") { e.preventDefault(); composer.setShowSlash(false); composer.setSlashFromMenu(false); return }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleComposerSubmit() }
  }, [composer, handleComposerSubmit])

  const handleComposerInput = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    composer.handleComposerInput(e)
  }, [composer])

  // ── Retry a failed/errored ask ─────────────────────────────────────────────
  // The per-turn "Ask again" affordance (mapMainTurns' `onAskAgain`). Same
  // orchestration main runs (`handleAskAgain`): a plain turn re-submits through
  // the shared `submitAsk`; a turn that carried attachments can't be replayed
  // (the files aren't re-uploadable), so it refills the composer for the user.
  const handleAskAgain = useCallback((turn: ThreadTurn) => {
    askAgain(turn, { submit: submitAsk, setDraft: composer.setDraft, composerRef: composer.composerRef })
  }, [composer, submitAsk])

  // ── Acting on a past prompt: copy / edit / retry (main parity) ─────────────
  // Copy is free — it changes nothing — so it works on any turn the viewer or a
  // peer actually spoke. It drops the trailing quoted passage (pasting "> …"
  // markup elsewhere is never what was meant); the excerpt has its own viewer.
  const handleCopyTurn = useCallback((turn: { id: string; query: string }) => {
    const body = splitQuotedSuffix(turn.query).body || turn.query
    if (!body) return
    void (async () => {
      try {
        await navigator.clipboard.writeText(body)
        setCopiedTurnId(turn.id)
        if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current)
        copiedTimerRef.current = setTimeout(() => setCopiedTurnId(null), COPIED_HINT_MS)
      } catch {
        showToast("Couldn't copy", "Your browser blocked clipboard access — select the text and copy it manually.")
      }
    })()
  }, [showToast])

  // Edit and retry both RE-ASK. The two surfaces diverge on what that means
  // because their persistence does:
  //   * PRIVATE (single-author) mirrors main exactly — truncate the on-screen
  //     thread at this turn AND rewind the persisted conversation to it (this
  //     surface's OWN `persistence.rewindToUserTurn` → DELETE …/turns/{id},
  //     NOT main's), so screen and DB agree, then re-ask. `dbTurnId` (threaded
  //     through hydrate above and the fresh-send map inside chatPersistence) is
  //     what lets a rehydrated turn be rewound at all.
  //   * GROUP is a shared multi-author feed: deleting everything after a turn
  //     would erase peers' messages, so a re-post is a NEW post, never a history
  //     rewind. It runs through `submitAsk`, which already carries the 2-mode
  //     response gate (untagged multi-human → silent post; @Sprntly / solo →
  //     reply) and mints a fresh turn id + client_message_id — so the reply gate
  //     is honoured and idempotency/echo-dedup stay intact (no double-render).
  const reAskFromTurn = useCallback((turn: ThreadTurn, nextQuery: string) => {
    const q = nextQuery.trim()
    if (!q) return
    setThread((prev) => {
      const idx = prev.findIndex((x) => x.id === turn.id)
      // Not found means the thread moved under us (a background answer landed, a
      // rehydrate) — drop the action rather than truncate at a guess.
      return idx === -1 ? prev : prev.slice(0, idx)
    })
    void persistence.rewindToUserTurn(convKey, turn.id, turn.dbTurnId)
    void submitAsk(q)
  }, [submitAsk, persistence, convKey])

  const handleRetryTurn = useCallback((turn: ThreadTurn) => {
    // Verbatim — including the quoted passage, which is part of the question.
    reAskFromTurn(turn, turn.query.trim())
  }, [reAskFromTurn])

  const handleEditTurn = useCallback((turnId: string) => setEditingTurnId(turnId), [])
  const handleCancelTurnEdit = useCallback(() => setEditingTurnId(null), [])
  const handleSubmitTurnEdit = useCallback((turn: ThreadTurn, text: string) => {
    setEditingTurnId(null)
    const body = text.trim()
    if (body.length < DRAFT_MIN_CHARS) return
    // The passage the original message replied to is kept: the editor owns your
    // words, not the excerpt. Re-composed the same way the composer would have.
    const { quote: repliedTo } = splitQuotedSuffix(turn.query)
    const next = buildQuotedMessage(body, repliedTo)
    // Saving without changing anything just closes the editor (Retry is for
    // re-running an identical question, deliberately).
    if (next === turn.query) return
    reAskFromTurn(turn, next)
  }, [reAskFromTurn])

  // A "Copied" timer left pending on unmount would setState on a dead surface.
  useEffect(() => () => { if (copiedTimerRef.current) clearTimeout(copiedTimerRef.current) }, [])

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
    const match = matchReportByTitle(reports, title)
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

  // The post-preview card handlers (send / re-preview / dock question settle)
  // come from the shared `useSlackShareCardHandlers` — main's ChatScreen calls
  // the SAME unit. This surface injects seams over its ONE conversation:
  // `patchTurn`/`getShare` reach the turn's card via `patchSlackShare`/
  // `threadRef`, and `getPendingShare`/`setPendingShare` its single
  // `pendingShare` state. `slackShareApi.send`/`.preview` ordering + the
  // re-preview-on-answer logic live in the shared unit.
  const slackGetShare = useCallback(
    (turnId: string) => threadRef.current.find((tn) => tn.id === turnId)?.slackShare,
    [],
  )
  const slackGetPendingShare = useCallback(() => pendingShareRef.current, [])
  const slackSetPendingShare = useCallback(
    (ps: PendingShareState | undefined) => setPendingShare(ps),
    [],
  )
  const { sendSlackShare, repreviewSlackShare, completeShareQuestion, cancelShareQuestion } =
    useSlackShareCardHandlers({
      patchTurn: patchSlackShare,
      getShare: slackGetShare,
      getPendingShare: slackGetPendingShare,
      setPendingShare: slackSetPendingShare,
    })

  // Card's × / decline — nothing posted; record it so the thread doesn't leave
  // "here's what I'll post" as the last word on a message that never went out.
  const cancelSlackShareCard = useCallback((turnId: string) => {
    const share = threadRef.current.find((tn) => tn.id === turnId)?.slackShare
    if (!share || share.resolved) return
    patchSlackShare(turnId, { resolved: { outcome: "cancelled" } })
  }, [patchSlackShare])

  // ── Assign: apply the dock popup's ambiguous picks (main-parity) ───────────
  // The batch's ONE landing (finish all questions, then the writes happen through
  // the ordinary fields endpoint, summary as its own agent turn) comes from the
  // shared `useAssignCompletion` — main's ChatScreen calls the SAME unit. This
  // surface injects seams over its ONE conversation: read/clear its
  // `pendingAssign`, toggle busy, append the summary turn (`newId`), finalize it.
  const assignGetPending = useCallback(() => pendingAssignRef.current, [])
  const assignClearPending = useCallback(() => setPendingAssign(undefined), [])
  const assignSetBusy = useCallback((b: boolean) => setBusy(b), [])
  const assignAppendReplyTurn = useCallback((reply: AskResponse) => {
    const noteId = newId()
    setThread((prev) => [...prev, { id: noteId, query: "", reply }])
  }, [])
  const assignFinalizeTurn = useCallback((turnId: string, reply: AskResponse) => {
    void finalizeConversationTurn(turnId, { reply }, convKey)
  }, [convKey, finalizeConversationTurn])
  const { completeAssign, cancelAssign } = useAssignCompletion({
    getPendingAssign: assignGetPending,
    clearPendingAssign: assignClearPending,
    setBusy: assignSetBusy,
    appendReplyTurn: assignAppendReplyTurn,
    finalizeTurn: assignFinalizeTurn,
  })

  // ── Host-bag assembly ──────────────────────────────────────────────────────
  const activeTab = useMemo(() => ({
    id: convKey, hydrating: hydrating && thread.length === 0,
    prdGenerating: !!meta.prdGenerating,
  }), [convKey, hydrating, thread.length, meta.prdGenerating])
  const lastLiveTurnIdx = thread.length - 1
  // The composer's one status line, same precedence main uses: a dictation
  // error outranks the busy hint (it's a stuck state, not a transient reply to
  // a keystroke), and the busy hint reuses main's exact copy. Built with
  // createElement because this host is a `.ts` file (no JSX).
  const composerHintNode = composer.voice.error
    ? composer.voice.error
    : composer.composerHint === "busy"
      ? createElement(Fragment, null, BUSY_ENTER_HINT_LEAD, createElement("b", null, "Stop"), BUSY_ENTER_HINT_TAIL)
      : null

  // The clarify card's open turn (carries `.clarify` + `.id`), if any.
  const pendingClarifyTurn = useMemo(() => {
    if (!pendingClarify) return null
    return threadRef.current.find((t) => t.id === pendingClarify.turnId) ?? null
  }, [pendingClarify, thread])
  const clarifyPopupOpen = !!pendingClarifyTurn && !clarifyPopupDismissed[pendingClarifyTurn.id]

  // Esc stops this conversation's streaming answer — parity with main's
  // ChatScreen Esc handler. The per-turn Stop button already fires
  // `engine.handleStopAsk`; this is the missing keybinding, nothing more.
  //
  // It yields to anything that owns Esc more locally, closing that first: the
  // attachment viewer, the slash palette, the `+` menu (main's three), plus this
  // surface's own dock popups (assign / share / clarify). Only when none is open
  // does Esc cancel the answer.
  useEffect(() => {
    if (!busy) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      if (viewerAttachment || composer.slashOpen || composer.plusMenuOpen) return
      if (pendingAssign || pendingShare || clarifyPopupOpen) return
      engine.handleStopAsk()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [busy, viewerAttachment, composer.slashOpen, composer.plusMenuOpen, pendingAssign, pendingShare, clarifyPopupOpen, engine.handleStopAsk])

  const mapDeps: MapMainTurnsDeps = useMemo(() => ({
    animatedTurnIds, askStartRef, resumedTurnsRef, lastLiveTurnIdx,
    busy,
    // The project GROUP surface has no Goal Analysis. Named explicitly rather
    // than omitted: `MapMainTurnsDeps` requires these so a surface cannot drop
    // them with a clean `tsc`, which is how the in-thread gates shipped inert.
    goalGateBusyTurnId: undefined,
    confirmGoalDefinition: undefined,
    approveGoalPlan: undefined,
    activeTab: { id: convKey, prdId: meta.prdId ?? null, prd: meta.prd ?? null, prdGenerating: !!meta.prdGenerating, pendingClarify: meta.pendingClarify },
    name, userInitials, skillForQuery: composer.skillForQuery,
    ticketSetActionState: (meta.ticketSetStatus === "generating" ? "running" : meta.ticketSetStatus === "ready" ? "ready" : meta.ticketSetStatus === "failed" ? "failed" : null),
    showInsightMsg: false, chatEvidenceExists: false,
    chatPrdExists: meta.prdId != null, chatPrdCtaWaiting: false, chatProtoPrdId: null, chatPrototypeReady: false,
    inlinePrdCards: false, inlinePrdAnchorIdx: null, insightCardNode: null, prdQuestionsNode: null,
    clarifyPopupOpen, pendingClarifyTurn,
    handleAskAgain, handleStopAsk: engine.handleStopAsk,
    // Acting on a past prompt (main parity). The mapper draws the Copy / Edit /
    // Ask-again row once these are present; ownership (peer turns get Copy only)
    // is enforced data-driven in the mapper via `turn.author`.
    editingTurnId, copiedTurnId,
    onCopyTurn: handleCopyTurn, onRetryTurn: handleRetryTurn, onEditTurn: handleEditTurn,
    onSubmitTurnEdit: handleSubmitTurnEdit, onCancelTurnEdit: handleCancelTurnEdit,
    submitClarifyAnswers, setViewerAttachment,
    openReportByTitle, openArtifactInPanel: (c) => onOpenArtifact?.(c), openChatArtifactItem,
    handleTicketSetAction: gen.handleTicketSetAction, handleOpenEvidence: () => {}, handleOpenPrd,
    handleViewPrototype: () => {}, handlePrototypeSettled: () => {},
    onSendSlackShare: sendSlackShare, onCancelSlackShare: cancelSlackShareCard, onPickSlackShareTarget: repreviewSlackShare,
    // The on-join greeting's lead/Show-more split. Only a greeting turn carries
    // the `MORE_MARKER`; every other turn returns null and stays on the default
    // reply ladder. main never passes it, so main rendering is unchanged.
    renderAgentBody: (turn: { reply?: { answer: string } | null }) =>
      turn.reply?.answer?.includes(MORE_MARKER)
        ? createElement(GreetingTurnBody, { answer: turn.reply.answer })
        : null,
  }), [lastLiveTurnIdx, busy, convKey, meta, name, userInitials, composer.skillForQuery, engine.handleStopAsk, clarifyPopupOpen, pendingClarifyTurn, submitClarifyAnswers, gen.handleTicketSetAction, onOpenArtifact, handleAskAgain, handleOpenPrd, openChatArtifactItem, openReportByTitle, setViewerAttachment, sendSlackShare, cancelSlackShareCard, repreviewSlackShare, editingTurnId, copiedTurnId, handleCopyTurn, handleRetryTurn, handleEditTurn, handleSubmitTurnEdit, handleCancelTurnEdit])

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
    handleComposerInput, handleComposerKeyDown, handleComposerSubmit,
    setPlusMenuActive: composer.setPlusMenuActive, setPlusMenuOpen: composer.setPlusMenuOpen,
    handlePlusMenuSelect: composer.handlePlusMenuSelect, setAttachments: composer.setAttachments,
    setPinnedSkill: composer.setPinnedSkill, handleFileSelect: composer.handleFileSelect,
    handleToggleVoice: composer.handleToggleVoice,
    // Empty-state greeting: unset → `ConversationView` renders its default
    // (main/private) copy unchanged.
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
    cancelAssign,
    sharePopupOpen: !!pendingShare,
    pendingShareState: pendingShare,
    completeShareQuestion,
    cancelShareQuestion,
    setQuestionDockEl,
    nextPrompts, submitAsk, showThreadView,
    threadScrollRef: scroll.threadScrollRef, handleThreadScroll: scroll.handleThreadScroll, setThreadContentEl: scroll.setThreadContentEl,
  }
}
