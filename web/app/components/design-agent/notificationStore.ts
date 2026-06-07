/**
 * P5-09 (F3 / BUILD-PHASES D22) — per-prototype ready-completion notification
 * persistence over `sessionStorage`.
 *
 * The live "Prototype ready" toast already fires in `DesignAgentDrawer` when a
 * generation completes (P1-12). This module adds only the persistence delta so
 * the notification survives a same-session page reload: one entry per
 * `prototypes.id`, flipped `pending → completed` when generation finishes, and
 * cleared on acknowledge (the drawer's mount effect re-shows + acknowledges any
 * completed-but-unacknowledged entry exactly once per reload).
 *
 * Why `sessionStorage`, not `localStorage`: the spec scopes survival to "within
 * the same session" — a brand-new browser session starts clean.
 *
 * SSR safety (Next.js renders the drawer server-side first): every access is
 * guarded by `typeof window !== "undefined"` + try/catch, so all functions
 * no-op gracefully when storage is unavailable (SSR / private mode / quota).
 * Extracted from the component so the persistence logic is unit-testable apart
 * from the DOM (the repo's vitest env is `node`).
 */

const KEY = "design-agent:notifications"

type NotificationStatus = "pending" | "completed"

type Entry = {
  prototypeId: number
  status: NotificationStatus
  sub: string
}

/** Public completed-entry shape (the subset the drawer needs to re-show). */
export type CompletedNotification = { prototypeId: number; sub: string }

/** Read the full entry array. Returns [] on any failure (SSR, bad JSON, quota). */
function readAll(): Entry[] {
  if (typeof window === "undefined") return []
  try {
    const raw = window.sessionStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as Entry[]) : []
  } catch {
    return []
  }
}

/** Persist the entry array. Silently no-ops when storage is unavailable. */
function writeAll(entries: Entry[]): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(entries))
  } catch {
    // SSR / private mode / quota — drop silently; persistence is best-effort.
  }
}

/** Insert or replace the single entry for a prototype id. */
function upsert(prototypeId: number, status: NotificationStatus, sub: string): void {
  const entries = readAll()
  const idx = entries.findIndex((e) => e.prototypeId === prototypeId)
  const next: Entry = { prototypeId, status, sub }
  if (idx >= 0) {
    entries[idx] = next
  } else {
    entries.push(next)
  }
  writeAll(entries)
}

/** Record a kickoff — a `pending` entry (NOT re-shown on mount). */
export function markPending(prototypeId: number): void {
  upsert(prototypeId, "pending", "")
}

/** Flip the entry to `completed` (or insert) so a reload can re-show it. */
export function markCompleted(prototypeId: number, sub: string): void {
  upsert(prototypeId, "completed", sub)
}

/** Remove the entry for a prototype id (clear-on-show / explicit acknowledge). */
export function acknowledge(prototypeId: number): void {
  const entries = readAll()
  const next = entries.filter((e) => e.prototypeId !== prototypeId)
  if (next.length !== entries.length) writeAll(next)
}

/** The completed (ready-but-unacknowledged) entries — `pending` is excluded. */
export function pendingCompleted(): CompletedNotification[] {
  return readAll()
    .filter((e) => e.status === "completed")
    .map((e) => ({ prototypeId: e.prototypeId, sub: e.sub }))
}

// ─── P6-05 (Decision-D(b)): per-page-load guards + last-replay-show record ───
//
// The shell replay (`DesignAgentNotificationReplay`) shows a completed-but-
// unacknowledged toast on EVERY authed page, not only the Design section. Two
// in-memory (module-level) guards make that safe WITHOUT touching the
// sessionStorage entry (which must survive reloads until the user acknowledges):
//
//   1. `seenThisLoad` — ids already shown during THIS page-load, so the replay
//      fires once per load even though AppShell re-mounts the replay on every
//      authed-route navigation. A real browser reload re-evaluates the module
//      (the Set starts empty), so the toast re-shows after a reload — exactly
//      the "re-shows across reloads until acknowledged" semantics (AC3).
//   2. `lastReplayShow` — the id + emitted {title, sub} of the LAST toast the
//      replay showed this load. Decision-D(b) acks on toast-clear, but the
//      single-slot toast (NavigationContext) can be overwritten by another
//      feature's `showToast`; acking on ANY clear would ack the wrong id. The
//      replay therefore acks ONLY when the cleared toast matches its own last
//      emission (`shouldAckOnClear`) — precise without touching NavigationContext.
//
// Both are deliberately NOT persisted to sessionStorage: a reload SHOULD re-show.

const seenThisLoad = new Set<number>()

/** Mark an id as shown during this page-load (so the shell replay does not
 *  re-fire on every authed-route mount within the same load). */
export function markSeenThisLoad(prototypeId: number): void {
  seenThisLoad.add(prototypeId)
}

/** Was this id already shown during this page-load? */
export function wasSeenThisLoad(prototypeId: number): boolean {
  return seenThisLoad.has(prototypeId)
}

/** The replay's last emitted toast this page-load (id + the shown title/sub). */
export type LastReplayShow = { prototypeId: number; title: string; sub: string }

let lastReplayShow: LastReplayShow | null = null

/** Record the toast the replay just emitted (called once per shown id; after a
 *  multi-entry replay the LAST call wins — the slot only ever holds the last). */
export function recordReplayShow(
  prototypeId: number,
  title: string,
  sub: string,
): void {
  lastReplayShow = { prototypeId, title, sub }
}

/** The replay's last emitted toast this page-load, or null if it showed none. */
export function getLastReplayShow(): LastReplayShow | null {
  return lastReplayShow
}

type ToastShape = { title: string; sub: string } | null

/**
 * Decision-D(b) ack precision: given the toast slot's PREVIOUS and CURRENT
 * values and the replay's last emission, return the id to acknowledge when (and
 * only when) the replay's OWN last-shown toast is the one that just cleared
 * (prev non-null → current null, and prev matches the recorded title+sub).
 * Returns null when some OTHER toast cleared (a competing `showToast` supplanted
 * the slot) — so the replay never acks an id it did not actually have visible.
 */
export function shouldAckOnClear(
  prevToast: ToastShape,
  currentToast: ToastShape,
  lastShow: LastReplayShow | null,
): number | null {
  if (!prevToast || currentToast) return null // not a clear of a shown toast
  if (!lastShow) return null
  if (prevToast.title === lastShow.title && prevToast.sub === lastShow.sub) {
    return lastShow.prototypeId
  }
  return null
}

/** Test seam: reset the in-memory per-page-load guards. A browser reload
 *  re-evaluates the module; tests call this to simulate a fresh page-load (the
 *  guards are NOT sessionStorage-backed by design). */
export function __resetPageLoadGuards(): void {
  seenThisLoad.clear()
  lastReplayShow = null
}
