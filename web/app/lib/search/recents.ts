import type { SearchItem } from "./types"

// ── Recent palette selections, per workspace, in localStorage ────────────────
//
// Shown when the palette opens with an empty query. Items are serializable by
// design (see types.ts), so the whole SearchItem is stored. Stale entries are
// harmless: path/screen actions always navigate, and resuming a deleted chat
// lands on the chat surface's own missing-conversation handling.

const KEY_PREFIX = "sprntly_palette_recents:"
export const MAX_RECENTS = 8

function key(workspaceId: string): string {
  return `${KEY_PREFIX}${workspaceId}`
}

export function getRecents(workspaceId: string): SearchItem[] {
  try {
    const raw = localStorage.getItem(key(workspaceId))
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (x): x is SearchItem =>
        x != null &&
        typeof x === "object" &&
        typeof x.id === "string" &&
        typeof x.title === "string" &&
        x.action != null,
    )
  } catch {
    return []
  }
}

export function pushRecent(workspaceId: string, item: SearchItem): void {
  try {
    const next = [
      item,
      ...getRecents(workspaceId).filter((r) => r.id !== item.id),
    ].slice(0, MAX_RECENTS)
    localStorage.setItem(key(workspaceId), JSON.stringify(next))
  } catch {
    /* ignore — recents are best-effort */
  }
}
