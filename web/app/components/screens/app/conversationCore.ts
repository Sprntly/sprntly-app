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
import type { ThreadTurn } from "./ChatScreen"

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
}
