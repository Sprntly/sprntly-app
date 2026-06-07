import type { ScreenId } from "../types"

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
  tickets: "/tickets",
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
