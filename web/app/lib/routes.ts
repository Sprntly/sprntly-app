import type { ScreenId } from "../types"
import {
  ONBOARDING_ANALYZING_SLUG,
  ONBOARDING_STEP_SLUGS,
} from "./onboarding/types"

/** Base path shared by the prototype generation landing page (`/prototype`)
 *  and the refresh-stable Design Agent canvas route (`/prototype/{prototype_id}`).
 *  The canvas coexists with the bare landing: `/prototype/page.tsx` handles the
 *  landing; `/prototype/[prototype_id]/page.tsx` is the canvas shell. Both live
 *  under this single base — there is no separate "/design" constant. */
export const PROTOTYPE_PATH = "/prototype"

/** Alias kept so callers that import CANVAS_BASE_PATH continue to work without
 *  a rename. It resolves to the same value as PROTOTYPE_PATH — canvas paths are
 *  now `/prototype/{id}` rather than `/design/{id}`. */
export const CANVAS_BASE_PATH = PROTOTYPE_PATH

/** Build the bare prototype landing path, threading the PRD context as `?prd=<id>`
 *  when present. With no PRD it returns the bare `/prototype`. Used by callers that
 *  navigate to the generation landing — distinct from canvasPath, which builds the
 *  id-bearing canvas path. Pure → unit-testable. */
export function prototypePath(prdId?: number | string | null): string {
  if (prdId == null || prdId === "") return PROTOTYPE_PATH
  return `${PROTOTYPE_PATH}?prd=${encodeURIComponent(String(prdId))}`
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
  "ob-coworkers": "/onboarding/coworkers",
  "ob-first-brief": "/onboarding/first-brief",
  "ob-analyzing": `/onboarding/${ONBOARDING_ANALYZING_SLUG}`,
  chat: "/",
  brief: "/brief",
  detail: "/evidence",
  prd: "/prd",
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
  // The prototype generation landing (sidebar nav target). The generate modal
  // opens in-place over the PRD screen; this route remains available for
  // direct navigation (e.g. bare /prototype with no ?prd= param shows an
  // empty state prompting the user to choose a PRD first).
  prototype: PROTOTYPE_PATH,
  // The refresh-stable canvas route base. The id-bearing path
  // (`/prototype/{prototype_id}`) is built by canvasPath() below.
  // pathForScreen("da-canvas") returns this base — canvas navigation goes
  // through goToCanvas(id)/canvasPath(id), never pathForScreen.
  "da-canvas": CANVAS_BASE_PATH,
}

const PATH_TO_SCREEN: Record<string, ScreenId> = {
  "/": "chat",
  "/brief": "brief",
  "/evidence": "detail",
  "/prd": "prd",
  "/past": "past",
  "/shipped": "shipped",
  "/settings": "settings",
  "/team": "team",
  "/sources": "sources",
  "/tickets": "tickets",
  // Both the bare landing (/prototype) and the id-bearing canvas path
  // (/prototype/{id}) map to the "prototype" screen so the prototype tab
  // stays highlighted while the canvas overlay is open. The id-bearing path
  // is handled by a prefix rule in screenIdFromPathname below (not by an
  // exact-match entry here, since the id segment is unbounded).
  [PROTOTYPE_PATH]: "prototype",
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
  // Prefix rule: /prototype/{digits} (the canvas overlay route) resolves to the
  // same "prototype" screen as the bare /prototype landing, so the prototype tab
  // stays highlighted while the canvas is open. The exact-match table handles
  // /prototype itself; this catches the id-bearing variant.
  if (/^\/prototype\/\d+$/.test(path)) return "prototype"
  return PATH_TO_SCREEN[path] ?? "chat"
}

export function pathForScreen(screen: ScreenId): string {
  const id = screen === "ondemand" ? "chat" : screen
  return SCREEN_PATH[id]
}

// ── canvas-ONLY refresh-stable route helpers ────────────────────────────────
// These are additive and scoped strictly to the canvas. They do NOT alter
// normalizePathname / screenIdFromPathname / pathForScreen above — the rest of
// the app's no-deep-URL nav is untouched.

/** Build the refresh-stable canvas path for a prototype, e.g.
 *  canvasPath(54) === "/prototype/54". The prototype_id is the ONLY state the
 *  canvas route carries. */
export function canvasPath(prototypeId: number | string): string {
  return `${CANVAS_BASE_PATH}/${prototypeId}`
}

/** Read the canvas prototype_id from a pathname, or null when the path is not
 *  the canvas route. Matches `/prototype/{id}` (one trailing numeric segment);
 *  the bare "/prototype", any non-numeric segment, or any deeper path returns null
 *  so the canvas never resolves against a malformed URL. */
export function prototypeIdFromCanvasPath(pathname: string | null): number | null {
  const path = normalizePathname(pathname)
  if (!path.startsWith(`${CANVAS_BASE_PATH}/`)) return null
  const rest = path.slice(CANVAS_BASE_PATH.length + 1)
  // Exactly one segment, all digits (prototype ids are positive integers).
  if (rest === "" || rest.includes("/") || !/^\d+$/.test(rest)) return null
  const id = Number(rest)
  return Number.isSafeInteger(id) ? id : null
}

/** Decide whether the canvas resolver should fetch — and which prototype_id —
 *  given the prototype_id read from the URL, whether the workspace has hydrated,
 *  and the id already mounted in the canvas. Returns the id to fetch, or null to
 *  do nothing. Gates on hydration (never resolve against an un-hydrated
 *  workspace) and skips a refetch when the canvas already shows that id. */
export function canvasResolveTarget(
  routeProtoId: number | null,
  workspaceHydrated: boolean,
  mountedCanvasId: number | null,
): number | null {
  if (routeProtoId == null) return null
  if (!workspaceHydrated) return null
  if (mountedCanvasId === routeProtoId) return null
  return routeProtoId
}
