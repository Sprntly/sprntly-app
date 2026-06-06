import type { ScreenId } from "../types"

/** Base path for the refresh-stable Design Agent canvas route. The full route
 *  carries the prototype_id (`/design/{prototype_id}`). This is the only
 *  deep-URL screen in the app's otherwise no-deep-URL nav. */
export const CANVAS_BASE_PATH = "/design"

/** App routes (no basePath). Onboarding uses `/onboarding/[step]`. */
export const SCREEN_PATH: Record<ScreenId, string> = {
  "ob-1": "/onboarding/1",
  "ob-2": "/onboarding/2",
  "ob-3": "/onboarding/3",
  "ob-4": "/onboarding/4",
  "ob-5": "/onboarding/5",
  "ob-6": "/onboarding/6",
  "ob-7": "/onboarding/7",
  "ob-8": "/onboarding/8",
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
  // The refresh-stable canvas route. The bare base path; the id-bearing path
  // (`/design/{prototype_id}`) is built by canvasPath() below.
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
  // Inverse for the canvas base path. screenIdFromPathname is left UNCHANGED —
  // it exact-matches, so the bare "/design" resolves to "da-canvas" while the
  // id-bearing "/design/{id}" falls through to "chat" (the canvas is a
  // full-screen overlay driven by canvasResolveTarget, not by currentScreen).
  [CANVAS_BASE_PATH]: "da-canvas",
}

for (let step = 1; step <= 8; step++) {
  PATH_TO_SCREEN[`/onboarding/${step}`] = `ob-${step}` as ScreenId
}

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

// ── canvas-ONLY refresh-stable route helpers ────────────────────────────────
// These are additive and scoped strictly to the canvas. They do NOT alter
// normalizePathname / screenIdFromPathname / pathForScreen above — the rest of
// the app's no-deep-URL nav is untouched.

/** Build the refresh-stable canvas path for a prototype, e.g.
 *  canvasPath(54) === "/design/54". The prototype_id is the ONLY state the
 *  canvas route carries. */
export function canvasPath(prototypeId: number | string): string {
  return `${CANVAS_BASE_PATH}/${prototypeId}`
}

/** Read the canvas prototype_id from a pathname, or null when the path is not
 *  the canvas route. Matches `/design/{id}` (one trailing numeric segment);
 *  the bare "/design", any non-numeric segment, or any deeper path returns null
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
