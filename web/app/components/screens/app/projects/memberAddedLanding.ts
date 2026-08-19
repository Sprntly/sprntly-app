// memberAddedLanding — the recipient side of "added to a project".
//
// The backend publishes a best-effort `member.added` liveness signal on the
// added person's OWN per-user channel `project:{id}:user:{uid}` (BOTH add paths:
// POST /members and POST /tag). This is the pure decision the consumer runs when
// that signal lands: WHERE — if anywhere — to bring the user, so they "land
// straight in its chats" (the invite-modal promise) instead of only finding the
// project on the next list load.
//
// Kept as a pure function (no React, no router) so the navigation INTENT is
// unit-testable in isolation from the subscription that feeds it — the caller
// turns a non-null target into the actual route push / tab switch.

/** The `member.added` DTO (backend `_MENTION_SIGNAL_DTO_KEYS`) — ids + names
 *  only, never message text or project content (AD-TNM2). */
export type MemberAddedSignal = {
  project_id?: number
  project_name?: string | null
  actor_name?: string | null
  kind?: string
}

export interface MemberAddedLandingContext {
  /** The project the user is currently viewing (the open detail route). */
  currentProjectId: number | string
  /** True when the user is already sitting in THIS project's private chat —
   *  landing them there again would be a no-op flicker. */
  alreadyInPrivateChat: boolean
  /** True when the user is actively typing (a focused composer/search field).
   *  A landing nav must never yank someone mid-task (the ticket's non-jarring
   *  requirement) — the membership still resolves on the next load. */
  busy: boolean
}

/**
 * The project id to land the user in, or `null` for "do nothing".
 *
 * Returns null when: the payload is not a well-formed signal; the user is mid-
 * task (`busy`); or the signal is for the project they are already sitting in
 * the private chat of. Otherwise returns the signalled `project_id` — the caller
 * pushes `/projects?id=<id>&chat=individual` (a different project) or switches
 * the open project's tab to the private chat (the same project).
 */
export function memberAddedLandingTarget(
  payload: unknown,
  ctx: MemberAddedLandingContext,
): number | null {
  if (!payload || typeof payload !== "object") return null
  const sig = payload as MemberAddedSignal
  if (typeof sig.project_id !== "number") return null
  // Never interrupt a task in progress.
  if (ctx.busy) return null
  // Already landed in this exact project's private chat → nothing to do.
  if (
    String(sig.project_id) === String(ctx.currentProjectId) &&
    ctx.alreadyInPrivateChat
  ) {
    return null
  }
  return sig.project_id
}
