"use client"

/**
 * The single-conversation seam contract for the shared chat ask-core.
 *
 * `ConversationHandle` is the ONE abstraction the per-conversation ask-core
 * (send / stream / stop / clarify / next-prompts / pending-send) reads and
 * writes through, so the exact same run logic can drive:
 *   - MAIN, where each handle targets one tab in the multi-conversation tab
 *     multiplexer (a background ask keeps writing to its captured tab after the
 *     user switches away — the handle is minted for that tab's id at send time),
 *     and
 *   - a PROJECT slot, where there is exactly one conversation and the handle is
 *     backed by plain single-conversation state.
 *
 * The point is that the ask-core NEVER hardcodes `setTabs(prev => prev.map(t =>
 * t.id === … ))`; it operates on a handle. Main builds a tab-backed handle so its
 * behaviour (and rendered DOM) is byte-unchanged; a project slot will build a
 * single-conversation handle later. This is EXTRACTION of main's real code
 * parameterized only on the store accessor — NOT the deleted `useConversation`
 * re-derivation, and it carries no `surface`/`SurfaceAdapter` argument.
 *
 * Placement note: this lives in `screens/app/` (not `chat-shell/`) because it
 * references the main screen's `ThreadTurn` model; the established dependency
 * direction is screens/app → chat-shell, so it cannot move down until a later
 * cleanup relocates `ThreadTurn`. Both consumers (`ChatScreen`,
 * `projects/ProjectMainThread`) live under `screens/app/`, so both can import it
 * here without inverting that direction.
 */

import type { PendingJob } from "../../../lib/jobResume"
import type { ThreadTurn, ChatTab } from "./ChatScreen"

/**
 * A live handle onto ONE conversation, minted by the host (main: per tab; a
 * project slot: once). The ask-core reads/writes turns, busy, and the
 * stop/asking flags exclusively through this — never through the host's own tab
 * array — so the run body is surface-agnostic.
 *
 * The handle intentionally exposes only conversation-scoped STATE accessors;
 * screen-level services that are the same for every conversation on a surface
 * (the async ask runner, the intent planner, toasts, next-prompt fetches) stay
 * with the ask-core, injected once — they are not per-conversation and do not
 * belong on the handle.
 */
export interface ConversationHandle {
  /** Stable local key for this conversation — main: the tab id; a project slot:
   *  its thread key. The ask/poll/persist/resume spine keys on it. */
  readonly key: string
  /** This conversation's current turns (read at write time, not captured, so a
   *  background ask sees turns added after it started). */
  getTurns(): ThreadTurn[]
  /** Patch this conversation's turns in place. */
  patchTurns(update: (turns: ThreadTurn[]) => ThreadTurn[]): void
  /** Composer-blocking in-flight state for THIS conversation only. */
  setBusy(busy: boolean): void
  /** Raise the user-initiated Stop flag (read by the running poller on its next
   *  tick to bail and discard any late answer). */
  markStopped(): void
  /** Whether the user has hit Stop on this conversation's in-flight ask. */
  isStopped(): boolean
  /** Drop this conversation from the in-flight ("asking") set — the immediate
   *  composer reclaim on Stop (the poller's finally also clears it; the
   *  double-clear is safe). */
  clearAsking(): void
  /** The persisted pending ask for this conversation (its id backs the
   *  best-effort backend cancel), or null when none is in flight. */
  pendingAsk(): PendingJob | null
  /** Whether an ask is currently in flight for this conversation (main: the tab
   *  is in the asking-set). Guards the post-answer suggestion publish so late
   *  chips never attach to a superseded turn. */
  isAsking(): boolean
  /** Whether this conversation is still live (main: the tab is still open). The
   *  other half of the suggestion late-arrival guard. */
  exists(): boolean
  /** Patch this conversation's per-conversation ARTIFACT metadata (the cached
   *  PRD / evidence / ticket-set state + their generating flags). Main: a
   *  `setTabs` field merge on this tab; a project slot: its own artifact state.
   *  Distinct from `patchTurns` (the thread) — this is the tab-metadata slice the
   *  generation flows write. */
  patchMeta(partial: Partial<ChatTab>): void
  /** Whether this conversation is the one currently SHOWN (main: the active
   *  tab). Generation flows gate their content-panel writes on this so a
   *  background conversation's generation never hijacks the shared panel. */
  isActive(): boolean
  /** This conversation's bound DB id, read fresh (main: the tab's `dbConvId`),
   *  or null when the row doesn't exist yet — the generation flows attach an
   *  artifact to it (or create-once when null). */
  dbConvId(): number | null
  /** This conversation's current per-conversation metadata (main: the tab), or
   *  null when it no longer exists — read by the flows that branch on the
   *  artifact state (e.g. the reply-footer re-run/reopen). */
  getMeta(): ChatTab | null
}

/**
 * The grounding params folded into every ask for one conversation — the subset
 * of `runAskGeneration`'s options a surface pins. Main resolves a tab's
 * conversation/PRD/evidence/ticket-set; a project surface pins its `project_id`.
 * Absent members simply don't ride the request.
 */
export interface AskGrounding {
  conversation_id?: number
  prd_id?: number
  project_id?: number
  evidence_id?: number
  ticket_set_id?: number
  /** Pluggable context source ({kind, params}) — the wire form of a surface's
   *  own context assembler. The project surfaces send
   *  `{ kind: "project", params: { project_id, surface } }`; the backend
   *  routes it to `ProjectContextAssembler` (membership-gated server-side).
   *  Absent on main, so the unscoped path is byte-identical. */
  context_source?: { kind: string; params?: Record<string, unknown> }
}

/**
 * Resolve the conversation id + grounding for a send, at REQUEST time. Main's
 * implementation reuses the tab's `dbConvId` (or creates the row once via the
 * shared persistence) and layers its PRD>evidence>ticket-set priority; a project
 * surface resolves its own conversation row and pins `project_id`. This is the
 * ONLY surface-divergent seam the ask run injects — the run body is otherwise
 * identical across surfaces.
 */
export type ResolveAskParams = (
  key: string,
  meta: { turnId: string; displayQuery: string },
) => Promise<{ convId: number | null; grounding: AskGrounding }>
