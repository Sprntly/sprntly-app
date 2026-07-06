/**
 * Backlog → prototype flip persistence.
 *
 * A backlog idea's prototype is LAZY: no PRD (and no prototype) exists until the
 * user clicks "Generate prototype". Per product: we do NOT proactively query the
 * backend for prototype readiness on the backlog. Instead, the moment a user
 * generates a prototype from a backlog idea, we record the idea's durable
 * `theme_id → prd_id` mapping here, so that idea's CTA flips from "Generate
 * prototype" to "View prototype" — and stays flipped across the navigate-away /
 * come-back round-trip and future reloads.
 *
 * Keyed by `theme_id` (the durable entity — backlog item ids can be regenerated
 * by a weekly re-analysis; the theme persists). Stored in `localStorage` because
 * a generated prototype is durable, not session-scoped.
 *
 * SSR safety (Next.js renders client screens server-side first): every access is
 * guarded by `typeof window !== "undefined"` + try/catch, so all functions no-op
 * gracefully when storage is unavailable (SSR / private mode / quota). Extracted
 * from the component so the logic is unit-testable apart from the DOM (the repo's
 * vitest env is `node`).
 */

const KEY = "backlog:prototypes"

type ThemePrdMap = Record<string, number>

/** Read the full theme_id → prd_id map. Returns {} on any failure. */
export function readBacklogPrototypes(): ThemePrdMap {
  if (typeof window === "undefined") return {}
  try {
    const raw = window.localStorage.getItem(KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {}
    // Keep only string→number entries (drops any malformed values).
    const out: ThemePrdMap = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof v === "number" && Number.isFinite(v)) out[k] = v
    }
    return out
  } catch {
    return {}
  }
}

/** Persist the map. Silently no-ops when storage is unavailable. */
function writeAll(map: ThemePrdMap): void {
  if (typeof window === "undefined") return
  try {
    window.localStorage.setItem(KEY, JSON.stringify(map))
  } catch {
    // SSR / private mode / quota — drop silently; persistence is best-effort.
  }
}

/** Record that `themeId`'s prototype has been generated (prd_id = `prdId`), so
 *  the idea's CTA flips to "View prototype". Overwrites any prior mapping. */
export function recordBacklogPrototype(themeId: string, prdId: number): void {
  if (!themeId || typeof prdId !== "number") return
  const map = readBacklogPrototypes()
  map[themeId] = prdId
  writeAll(map)
}

/** The prd_id whose prototype was generated from this theme, or null if the user
 *  has not generated one yet (→ the idea still shows "Generate prototype"). */
export function getBacklogPrototypePrdId(themeId: string | null | undefined): number | null {
  if (!themeId) return null
  const map = readBacklogPrototypes()
  const id = map[themeId]
  return typeof id === "number" ? id : null
}
