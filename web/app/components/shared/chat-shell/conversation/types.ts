"use client"

/**
 * The single-conversation boundary contracts — the frozen target shape the
 * shared chat interface decomposes toward. ALL THREE chat surfaces (main,
 * project-private, project-group) drive ONE presentation (`ConversationView`)
 * over ONE engine (`useConversation`), differing only through a non-visual
 * `SurfaceAdapter`.
 *
 * SCOPE OF THIS FILE (Step A): these two interfaces are pinned CONTRACT-ONLY —
 * typed and documented, wired to no product path yet. Nothing consumes them in
 * Step A; the presentation lift still runs off the main screen's inline engine
 * (see `screens/app/ConversationView.tsx`'s transitional `ConversationViewProps`).
 * The point is to freeze the boundary before decomposition so the engine
 * extraction (Step B) and the private/group rebuilds (Steps C/D) find a contract,
 * not a construction site. Mirrors the established CONTRACT-ONLY pattern in the
 * sibling `chat-shell/types.ts` (its `NEXT-WAVE` seams).
 *
 * DELIBERATELY ABSENT: there is NO `mode: "streamed" | "backgrounded"` seam. All
 * three surfaces are client-driven and streamed — the agent replies to every
 * message on every surface — so the engine has exactly one run path. Group's
 * post-and-receive / realtime / mention-gate behaviours are NOT modelled here;
 * they are deferred and reintroduced incrementally in later work, at which point
 * this contract (not the shell) is where they land.
 *
 * These types are surface-agnostic on purpose: this module imports only from the
 * shared chat-shell + lib layers, never from a screen — the established
 * dependency direction (screens/app → chat-shell) is preserved.
 */

import type { RefObject } from "react"
import type { AskResponse } from "../../../../lib/api"
import type { AttachmentRef } from "../types"
import type { ChatPersistence } from "../../../../lib/chatPersistence"
import type { ClarifyAnswer, ClarifyQuestion } from "../../ClarifyQuestionsCard"
import type { NextPromptsAdapter } from "../useNextPrompts"
import type { ChatIntentExecutorAdapter, ChatSurfaceKind } from "../types"
// The canonical turn model (the plan pins main's `ThreadTurn` as the one turn
// shape). Imported here as a type only; a later cleanup relocates the definition
// out of the main screen into this shared module.
import type { ThreadTurn } from "../../../screens/app/ChatScreen"

// ── Engine sub-shapes ────────────────────────────────────────────────────────

/**
 * The optimistic just-sent turn awaiting its first server ack — the source of
 * main's `pending-send` bubble. Single-conversation, so (unlike the main
 * screen's per-tab `pendingSend`) it carries no `tabId`; the engine owns exactly
 * one conversation.
 */
export interface ConversationPendingSend {
  query: string
  attachments: { name: string }[]
  /** The turn's own clock, handed to the wait ladder so a cache hit opens on
   *  rung 0 rather than a spinner that flickers. */
  startedAt: number
}

/**
 * The clarify-gate seam: the open sufficiency-check batch (if any) plus its
 * resolution. One conversation carries at most one open clarify batch.
 */
export interface ConversationClarify {
  /** The turn the batch belongs to. */
  turnId: string
  questions: ClarifyQuestion[]
  busy: boolean
  /** Submit the user's answers (or an empty list = "skip everything"). */
  submit(answers: ClarifyAnswer[]): void | Promise<void>
  /** Dismiss without answering. */
  dismiss(): void
}

/** The per-conversation handle the engine hands `dispatchIntent`, so a shared
 *  action can write THIS conversation's turns without the engine exposing its
 *  internals. A surface composes it with persistence before passing it to the
 *  action's `ActionConfig.emitTurn`. */
export interface ConversationActionContext {
  /** Render a fully-formed, settled turn into this conversation. */
  emitTurn(turn: ThreadTurn): void
  /** Run an async command turn against this conversation: seed an optimistic
   *  turn, mark the engine busy, await the worker's turn-patch (reply + any turn
   *  extras, e.g. a Slack preview card), settle the turn, and clear busy. Returns
   *  the settled turn's id + reply so the caller can persist it (server-only). The
   *  engine owns the render/busy lifecycle; persistence is the surface's. */
  runActionTurn(
    query: string,
    worker: () => Promise<Partial<ThreadTurn> & { reply: AskResponse }>,
  ): Promise<{ turnId: string; reply: AskResponse }>
}

/** The normalized send's extras a surface may ride on `submit`. */
export interface ConversationSubmitOptions {
  /** Resolved attachments (extracted text + storage handle) — folded into the
   *  ask context server-side and shown as chips on the turn. */
  attachments?: AttachmentRef[]
  /** Idempotency key persisted server-side (`ask_jobs.client_message_id`); the
   *  hook mints the turn id as the key when a surface omits it. */
  clientMessageId?: string
}

/** Post-answer next-prompt suggestions for this one conversation. */
export interface ConversationNextPrompts {
  suggestions: string[]
  /** Send a suggested prompt as the next turn. */
  onPick(prompt: string): void | Promise<void>
}

/** Hydration / resume flags for this conversation. */
export interface ConversationResume {
  /** History is still loading (the row opened instantly on click). */
  hydrating: boolean
  /** Turn ids restored from history/an in-flight run — excluded from the
   *  first-render typing animation so a reload doesn't replay it. */
  resumedTurnIds: ReadonlySet<string>
}

/**
 * DELTA (project reality wins — see the frozen-contract note at the head of the
 * file). The three render-mutated refs that ride the boundary between the async
 * run and the render pass:
 *   - `animatedTurnIds` — the typing-animation dedup Set; `mapMainTurns` MUTATES
 *     it during the render pass (a fresh reply animates exactly once).
 *   - `askStartRef`     — per-turn ask clocks feeding the wait ladder.
 *   - `resumedTurnsRef`  — turn ids restored/re-attached, read at render.
 *
 * These are HOST-OWNED `RefObject`s, NOT engine `useState`: `useConversation`
 * mutates them inside its async run (exactly as `useMainConversation` already
 * receives them), and the SAME ref objects are threaded into the render-time
 * `mapMainTurns` call. Folding them into engine state would make the render-pass
 * `.add()` an illegal setState-in-render and desync the streamed-vs-replay dedup.
 * The frozen `ConversationResume.resumedTurnIds` (an immutable snapshot) is the
 * READ-model of `resumedTurnsRef`; the live ref is the write-model.
 *
 * A single-conversation surface (project) may let the engine mint these
 * internally; main INJECTS its wrapper-owned refs so the tab wrapper can also
 * thread them into `mapMainTurns` and its multi-tab resume effect. Hence the
 * adapter carries them as an optional injected seam (absent → engine-created).
 */
export interface ConversationRenderRefs {
  animatedTurnIds: RefObject<Set<string>>
  askStartRef: RefObject<Map<string, number>>
  resumedTurnsRef: RefObject<Set<string>>
}

/**
 * DELTA (project reality wins). The frozen `SurfaceAdapter` assumed a FIXED
 * `conversationKey` per mount. Main's reality violates that: a send can SPAWN a
 * new conversation (a fresh tab), so the target a send lands on is resolved
 * per-send. This seam models that resolution — main spawns/reuses a tab and
 * returns the rollback anchors an extraction failure needs; a single-conversation
 * surface returns its one fixed key with no spawn. `useConversation.submit`
 * calls it after the optimistic pending-send and before the real-turn commit.
 */
export interface ConversationSendTarget {
  /** The conversation key this send writes to (main: the resolved tab id). */
  targetKey: string
  /** True when a fresh conversation/tab was spawned to hold this send. */
  spawned: boolean
  /** Rollback anchors (main: the previously-active tab + its title) so an
   *  extract failure can restore the prior surface state. */
  prevActiveKey: string | null
  prevTitle: string | null
}

// ── The engine output (useConversation → ConversationEngine) ─────────────────

/** The grounding params folded into every ask for this conversation — the
 *  subset of `runAskGeneration`'s options a surface pins (main a tab's PRD /
 *  evidence / ticket-set; a project surface its `project_id`). Absent members
 *  simply don't ride. */
export interface ConversationAskParams {
  prd_id?: number
  project_id?: number
  evidence_id?: number
  ticket_set_id?: number
}

/**
 * The output of `useConversation(adapter)` — the single-conversation turn/run
 * engine. Owns ONE conversation (no tab map; the main screen's tab wrapper mounts
 * one engine per tab in a later step). Implemented in Step B as a self-contained
 * hook; main stays on its inline path until it adopts the hook.
 */
export interface ConversationEngine {
  /** This conversation's turns, in the canonical `ThreadTurn` model. The
   *  presentation (`ConversationView`) maps them to the shell's render model —
   *  the map is surface-specific, so it lives at the consumer, not the engine. */
  turns: ThreadTurn[]
  /** Composer-blocking in-flight state for THIS conversation only. */
  busy: boolean
  /** The optimistic just-sent turn awaiting its first ack, or null. */
  pendingSend: ConversationPendingSend | null
  /** Patch a turn in place by id — the interactive card handlers use it (a Slack
   *  preview card flipping busy → sent/cancelled). No-op when the id is unknown. */
  patchTurn(id: string, patch: Partial<ThreadTurn>): void
  /** Submit the composer draft as a new turn/run. `opts` carry the normalized
   *  send's extras (resolved attachments + the idempotency key) for surfaces
   *  whose send pipeline needs them (project chats ride `/v1/ask` with a
   *  `client_message_id`); main omits them. */
  submit(draft: string, opts?: ConversationSubmitOptions): void
  /** Stop the in-flight run (Esc / stop button). */
  stop(): void
  /** The open clarify gate, or null when none is holding. */
  clarify: ConversationClarify | null
  /** Post-answer next-prompt suggestions. */
  nextPrompts: ConversationNextPrompts
  /** Hydration / resumed-run flags. */
  resume: ConversationResume
}

// ── The per-surface seam (useConversation input) ─────────────────────────────

/**
 * The ONLY per-surface seam — all NON-visual. Each of the three surfaces differs
 * here and nowhere else; the group adapter is ~identical to the private one,
 * pointed at the group's shared conversation instead of a per-user one. Consumed
 * by `useConversation` in Step B; the private/group rebuilds supply their own in
 * later steps.
 */
export interface SurfaceAdapter {
  /** Who the conversation belongs to + how the user/turns are keyed. */
  identity: {
    surface: ChatSurfaceKind
    /** Normalized to a number; absent on main. */
    projectId?: number | null
    userName: string
    userInitials: string
    /** The tenant/company scope `runAskGeneration` + job-resume key by. */
    company: string
    /** The stable local key for THIS conversation — main a tab id, a project
     *  surface its thread id. The ask/poll/resume/persistence spine keys on it. */
    conversationKey: string
  }
  /** The turn writer — main writes client+server, project surfaces write
   *  server-only; both satisfy `createChatPersistence`. */
  persistence: ChatPersistence
  /** Loads this conversation's prior turns on mount (canonical `ThreadTurn`). */
  loadHistory(): Promise<ThreadTurn[]>
  /** Grounding folded into every ask (a tab's PRD / a project's id). */
  askParams?: ConversationAskParams
  /** The surface's next-prompt fetch (main → `chatSuggestionsApi.next`; a project
   *  surface supplies its own thread-scoped fetch). Absent → no suggestions. */
  suggestions?: NextPromptsAdapter
  /** Command-intent dispatch for this surface: resolve the message's intent and,
   *  when it is a command, run the SHARED action layer config'd for this surface
   *  (never a re-implementation). `submit` calls it first, handing the engine's
   *  per-conversation `ConversationActionContext`, and short-circuits the ask when
   *  it reports the message HANDLED. Absent → every send is an ask. */
  dispatchIntent?(draft: string, ctx: ConversationActionContext): Promise<boolean> | boolean
  /** Resolve an open clarify batch for this conversation (surface-specific: main
   *  re-enters generation, a project surface answers its gate). Absent → the
   *  clarify seam stays inert. */
  submitClarify?(turnId: string, answers: ClarifyAnswer[]): void | Promise<void>
  /** The surface's chat-intent flow bodies (endpoints per surface). Every slot
   *  optional — a surface provides only the intents it implements. */
  intentAdapter?: ChatIntentExecutorAdapter
  /** DELTA (project reality wins). Host-owned render refs threaded into BOTH the
   *  async run AND the render-time `mapMainTurns`. Main injects its wrapper's
   *  refs (so the tab wrapper shares them with `mapMainTurns` + the multi-tab
   *  resume effect); absent → the engine mints its own (single-conversation). */
  renderRefs?: ConversationRenderRefs
  /** DELTA (project reality wins). Per-send target resolution — main spawns/reuses
   *  a tab; a single-conversation surface returns its one fixed key. Absent → the
   *  send always targets `identity.conversationKey`. NOTE: the turn STORE itself
   *  is likewise an injected seam for main (the tab list, keyed by target),
   *  because main's background asks write to INACTIVE conversations; the engine
   *  therefore does not own an internal single-conversation `useState` store when
   *  driving main. A single-conversation surface owns its store internally. */
  resolveSendTarget?(newTurnId: string): ConversationSendTarget
}

// Re-export the reply shape so a run-status consumer can spell it from here.
export type { AskResponse }
