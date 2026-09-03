import type { ScreenId } from "../types"
import { ONBOARDING_STEP_SLUGS } from "./onboarding/types"

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

/** Base path for the flat Projects surface (AD-P14 — `?id=<id>` query param,
 *  no `[id]` dynamic segment; mirrors `PROTOTYPE_PATH`). */
export const PROJECTS_PATH = "/projects"

/** Build the projects path, threading a project id as `?id=<id>` when
 *  present (opens the detail view). With no id it returns the bare
 *  `/projects` list route. `opts.chat` additionally appends `&chat=` —
 *  which chat tab to land on (`"individual"` for the fork-to-private-chat
 *  nav; `"group"` for parity) — but ONLY when a project id is present; with
 *  no id, `chat` is ignored (there is no detail view to select a tab on).
 *  `opts.prd` additionally appends `&prd=<id>` — the PRD to land the
 *  project's content panel on, consumed by `ProjectDetailScreen`'s one-shot
 *  `?prd=` restore effect (accepts a bare `prd_id`; the same param also
 *  accepts a `public_id` uuid when built elsewhere, but this helper always
 *  threads whichever value the caller passes through verbatim). Ignored
 *  when there is no project id, same as `chat`. The no-`opts` call and the
 *  `chat`-only call are byte-identical to the base forms — every existing
 *  caller is unaffected. Pure → unit-testable. */
export function projectPath(
  projectId?: number | string | null,
  opts?: { chat?: "group" | "individual"; prd?: number | string | null },
): string {
  if (projectId == null || projectId === "") return PROJECTS_PATH
  let path = `${PROJECTS_PATH}?id=${encodeURIComponent(String(projectId))}`
  if (opts?.chat) path += `&chat=${opts.chat}`
  if (opts?.prd != null && opts.prd !== "") path += `&prd=${encodeURIComponent(String(opts.prd))}`
  return path
}

/** App routes (no basePath). Onboarding uses `/onboarding/[slug]`. */
export const SCREEN_PATH: Record<ScreenId, string> = {
  "ob-company": "/onboarding/company",
  "ob-connectors": "/onboarding/connectors",
  "ob-review": "/onboarding/review",
  "ob-personalize": "/onboarding/personalize",
  chat: "/",
  chats: "/history",
  artifacts: "/artifacts",
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
  ideation: "/backlog",
  // Templates and Skills moved INTO Settings (2026-08-27), so their `?section=`
  // link is the destination — same shape as `connectors` above. `/templates`
  // and `/skills` still exist purely as redirects onto these (see each route's
  // page.tsx); nothing in the app should route TO them.
  templates: "/settings?section=templates",
  skills: "/settings?section=skills",
  // Flat route + `?id=<id>` (AD-P14) — no per-id dynamic segment, exactly the
  // `/prototype?prd=<id>` pattern above. `ProjectsScreen` (list) renders when
  // there is no `id`; the `?id=<id>` → detail branch lands with a follow-up ticket.
  projects: "/projects",
}

const PATH_TO_SCREEN: Record<string, ScreenId> = {
  "/": "chat",
  "/history": "chats",
  "/artifacts": "artifacts",
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
  "/backlog": "ideation",
  // The OLD path still resolves to the same screen. `/backlog/page.tsx`
  // redirects it, but a race between that redirect and the shell's own
  // `?company=` rewrite would otherwise leave the rail unhighlighted for a
  // frame — and any code deriving the screen from the pathname must not read
  // an old link as "no screen".
  "/ideation": "ideation",
  // NO "/templates" or "/skills" ENTRIES. Both are redirect stubs now, not
  // screens: a pathname-derived screen id for them would highlight a rail item
  // that no longer exists, for the one frame before the redirect fires.
  // The `?id=` query param rides on top of this same path — pathname-based
  // screen derivation ignores it, same as `/prototype`'s `?prd=`.
  "/projects": "projects",
}

// Inverse map for the numbered onboarding routes (slug → "ob-<slug>" ScreenId).
for (const slug of ONBOARDING_STEP_SLUGS) {
  PATH_TO_SCREEN[`/onboarding/${slug}`] = `ob-${slug}` as ScreenId
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
