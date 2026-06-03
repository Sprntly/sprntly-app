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
