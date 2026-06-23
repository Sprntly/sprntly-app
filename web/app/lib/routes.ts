import type { ScreenId } from "../types"
import {
  ONBOARDING_ANALYZING_SLUG,
  ONBOARDING_STEP_SLUGS,
} from "./onboarding/types"

/** Base path for the prototype surface. The prototype canvas renders in-tab at
 *  `/prototype?prd=<id>` (the PRD context is carried as a query param); there is
 *  no per-id dynamic segment. The bare `/prototype` shows an empty state
 *  prompting the user to choose a PRD first. */
export const PROTOTYPE_PATH = "/prototype"

/** Build the prototype path, threading the PRD context as `?prd=<id>` when
 *  present. With no PRD it returns the bare `/prototype`. This is the single
 *  destination for opening a prototype: the in-tab canvas resolves the PRD's
 *  ready prototype from the `?prd=` param. Pure → unit-testable.
 *
 *  `opts.generate` appends a one-shot `&generate=1` (or `?generate=1` when there
 *  is no prd) — the explicit-generate-intent signal a "Generate Prototype" nav
 *  carries so PrototypeRoute opens the generate panel directly instead of landing
 *  on the empty-state gate. The route CONSUMES the param on mount (strips it via
 *  router.replace) so a later refresh after dismiss does not re-open the panel.
 *  Omitted/false → no signal, the existing default-closed gate behaviour. The
 *  default no-opts call keeps the bare `?prd=` form for all view-intent callers. */
export function prototypePath(
  prdId?: number | string | null,
  opts?: { generate?: boolean },
): string {
  const base =
    prdId == null || prdId === ""
      ? PROTOTYPE_PATH
      : `${PROTOTYPE_PATH}?prd=${encodeURIComponent(String(prdId))}`
  if (!opts?.generate) return base
  const sep = base.includes("?") ? "&" : "?"
  return `${base}${sep}generate=1`
}

/** Read the PRD id carried in the prototype page's `?prd=` query param, or null
 *  when absent / malformed. Accepts the raw value from `useSearchParams().get`
 *  (string | null). PRD ids are positive integers; anything else → null so the
 *  page never kicks generation against a bad id. Pure → unit-testable. */
export function prdIdFromPrototypeSearch(raw: string | null): number | null {
  if (raw == null || raw === "" || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return Number.isSafeInteger(id) && id > 0 ? id : null
}

/** App routes (no basePath). Onboarding uses `/onboarding/[slug]`. */
export const SCREEN_PATH: Record<ScreenId, string> = {
  "ob-business-info": "/onboarding/business-info",
  "ob-metrics": "/onboarding/metrics",
  "ob-connectors": "/onboarding/connectors",
  "ob-first-brief": "/onboarding/first-brief",
  "ob-analyzing": `/onboarding/${ONBOARDING_ANALYZING_SLUG}`,
  chat: "/",
  chats: "/chats",
  brief: "/brief",
  detail: "/evidence",
  ondemand: "/",
  past: "/past",
  shipped: "/shipped",
  settings: "/settings",
  team: "/team",
  // connectors: route deleted in commit A — Settings → Connectors is the
  // sole surface. The "connectors" ScreenId is kept in the type union for
  // the dormant ConnectorsScreen.tsx (see commit A note in that file).
  connectors: "/settings?section=connectors",
  sources: "/sources",
  tickets: "/tickets",
  // The prototype surface (sidebar nav target). The canvas renders in-tab at
  // `/prototype?prd=<id>`; bare `/prototype` with no `?prd=` shows an empty state
  // prompting the user to choose a PRD first.
  prototype: PROTOTYPE_PATH,
  backlog: "/backlog",
}

const PATH_TO_SCREEN: Record<string, ScreenId> = {
  "/": "chat",
  "/chats": "chats",
  "/brief": "brief",
  "/evidence": "detail",
  "/past": "past",
  "/shipped": "shipped",
  "/settings": "settings",
  "/team": "team",
  "/sources": "sources",
  "/tickets": "tickets",
  // The prototype surface maps to the "prototype" screen so the prototype tab
  // stays highlighted. The PRD context rides as a `?prd=` query param, which
  // pathname-based screen derivation ignores — the path is always `/prototype`.
  [PROTOTYPE_PATH]: "prototype",
  "/backlog": "backlog",
}

// Inverse map for the numbered onboarding routes (slug → "ob-<slug>" ScreenId).
for (const slug of ONBOARDING_STEP_SLUGS) {
  PATH_TO_SCREEN[`/onboarding/${slug}`] = `ob-${slug}` as ScreenId
}
// The unnumbered loader route resolves to its own ScreenId.
PATH_TO_SCREEN[`/onboarding/${ONBOARDING_ANALYZING_SLUG}`] = "ob-analyzing"

/** Normalize pathname from `usePathname()` (strip trailing slash). */
export function normalizePathname(pathname: string | null): string {
  if (!pathname || pathname === "") return "/"
  const trimmed = pathname.replace(/\/+$/, "")
  return trimmed === "" ? "/" : trimmed
}

export function screenIdFromPathname(pathname: string | null): ScreenId {
  const path = normalizePathname(pathname)
  return PATH_TO_SCREEN[path] ?? "chat"
}

export function pathForScreen(screen: ScreenId): string {
  const id = screen === "ondemand" ? "chat" : screen
  return SCREEN_PATH[id]
}
