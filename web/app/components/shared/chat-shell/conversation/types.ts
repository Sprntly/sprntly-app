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

import type { AskResponse } from "../../../../lib/api"
import type { ChatPersistence } from "../../../../lib/chatPersistence"
import type { ClarifyAnswer, ClarifyQuestion } from "../../ClarifyQuestionsCard"
import type {
  ChatIntentExecutorAdapter,
  ChatSurfaceKind,
  ChatTranscriptTurn,
} from "../types"

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

// ── The engine output (useConversation → ConversationEngine) ─────────────────

/**
 * The output of `useConversation(adapter)` — the single-conversation turn/run
 * engine. Owns ONE conversation (no tab map; the main screen's tab wrapper mounts
 * one engine per active tab). CONTRACT-ONLY in Step A; implemented in Step B.
 */
export interface ConversationEngine {
  /** This conversation's turns, already in the shell's render model. */
  turns: ChatTranscriptTurn[]
  /** Composer-blocking in-flight state for THIS conversation only. */
  busy: boolean
  /** The optimistic just-sent turn awaiting its first ack, or null. */
  pendingSend: ConversationPendingSend | null
  /** Submit the composer draft as a new turn/run. */
  submit(draft: string): void
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
 * pointed at the group's shared conversation instead of a per-user one.
 * CONTRACT-ONLY in Step A; consumed by `useConversation` in Step B and by the
 * private/group rebuilds in Steps C/D.
 */
export interface SurfaceAdapter {
  /** Who the conversation belongs to + how the user renders. */
  identity: {
    surface: ChatSurfaceKind
    /** Normalized to a number; absent on main. */
    projectId?: number | null
    userName: string
    userInitials: string
  }
  /** The turn writer — main writes client+server, project surfaces write
   *  server-only; both satisfy `createChatPersistence`. */
  persistence: ChatPersistence
  /** Loads this conversation's prior turns on mount. */
  loadHistory(): Promise<ChatTranscriptTurn[]>
  /** Re-attach to an already-kicked-off run after a reload. Absent on a surface
   *  with no resumable run. */
  resume?(): void | Promise<void>
  /** Grounding params folded into every ask (e.g. `{ project_id }`); absent on
   *  main. */
  askParams?: Record<string, unknown>
  /** The surface's chat-intent flow bodies (endpoints per surface). Every slot
   *  optional — a surface provides only the intents it implements. */
  intentAdapter?: ChatIntentExecutorAdapter
}

// Re-export the reply shape so a run-status consumer can spell it from here.
export type { AskResponse }
