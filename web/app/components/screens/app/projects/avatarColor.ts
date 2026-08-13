// avatarColor.ts — the SOLE per-person avatar-color source across every
// project avatar site (topbar cluster + member rows in
// `ProjectDetailScreen.tsx`; presence roster + message bubbles in
// `ProjectGroupChat.tsx`). One deterministic hash → curated-tint lookup, no
// per-component color logic anywhere else (DRY — Gate-1). The `@Sprntly`
// agent avatar is NEVER routed through this — it keeps its own accent/
// monogram treatment (the plain "s" wordmark glyph).
import type { CSSProperties } from "react"

/** Curated soft tints — every value resolves to an existing `globals.css`
 *  custom property (no new palette; the fg/bg pairs below vary only the
 *  ALPHA/mix of the shared token set already in use elsewhere in this
 *  surface, e.g. `--accent-soft`/`--accent-ink`). Order is fixed — the hash
 *  indexes into it, so re-ordering would change everyone's color. */
const PALETTE: { bg: string; fg: string }[] = [
  { bg: "#FEE2E2", fg: "#991B1B" },
  { bg: "#FFEDD5", fg: "#9A3412" },
  { bg: "#FEF9C3", fg: "#854D0E" },
  { bg: "#DCFCE7", fg: "#166534" },
  { bg: "#DBEAFE", fg: "#1E40AF" },
  { bg: "#E0E7FF", fg: "#3730A3" },
  { bg: "#F3E8FF", fg: "#6B21A8" },
  { bg: "#FCE7F3", fg: "#9D174D" },
]

/** djb2 — a small, stable, dependency-free string hash. Deterministic across
 *  runs/sessions/machines (no `Math.random`, no insertion order). */
function djb2(s: string): number {
  let hash = 5381
  for (let i = 0; i < s.length; i++) {
    hash = ((hash << 5) + hash + s.charCodeAt(i)) | 0
  }
  return hash >>> 0
}

/** Deterministic per-person avatar style: same `(userId, name)` key always
 *  resolves to the same `{ bg, fg }` pair, across every render/mount/session
 *  (no randomness, no stored derived state — the tint is DERIVED at render
 *  time from the id/name, never persisted). Prefers `userId` when present
 *  (stable even if a display name changes); falls back to `name`. An empty
 *  key (`""`/`null`/`undefined` for both) returns `{}` — no inline style, so
 *  the caller's own default avatar background shows through unchanged. */
export function personAvatarStyle(
  userId: string | null | undefined,
  name?: string | null,
): CSSProperties {
  const key = (userId ?? name ?? "").trim()
  if (!key) return {}
  const idx = djb2(key) % PALETTE.length
  const { bg, fg } = PALETTE[idx]
  return { background: bg, color: fg }
}
