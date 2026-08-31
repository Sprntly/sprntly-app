import {
  LEAF_LABELS,
  SETTINGS_NAV,
  SETTINGS_PANES,
  type SettingsSectionId,
} from "../../components/screens/app/settings/SettingsLayout"
import type { SearchItem } from "./types"

// ── Static searchable surface: pages, settings panes, actions ────────────────
//
// Pages mirror the sidebar's nav surfaces (SCREEN_PATH in lib/routes.ts);
// routes the design reset retired (tickets, past, shipped, roadmap) are
// deliberately absent — surfacing them in search would resurrect surfaces that
// were taken away on purpose. Settings items DERIVE from SETTINGS_NAV so the
// palette can never drift from what the settings sidebar actually shows
// (including the everyone-sees-admin choice documented there).
//
// THIS LIST IS HAND-MAINTAINED AND THE RAIL IS NOT, which is how Projects and
// Backlog came to be missing: both are live rail items, and neither was ever
// added here when they arrived. Backlog in particular read as retired — it
// WAS, under the reset, and then came back as a rail item without this list
// hearing about it. A rail item that search cannot find is the worst case of
// the two, because search is the only door to half the app now: add an entry
// here whenever a screen gains a rail item.

/** Extra matchable aliases per settings pane, beyond its nav label. */
const SETTINGS_KEYWORDS: Partial<Record<SettingsSectionId, string[]>> = {
  profile: ["account", "name", "avatar", "role"],
  "comms-brief": ["email", "notifications", "top insights", "slack"],
  "product-category": ["product", "category"],
  "company-profile": ["mission", "icp", "tone", "voice", "company"],
  process: ["planning", "sprint", "cadence"],
  metrics: ["kpi", "kpis", "definitions", "measures"],
  "business-context": ["context", "lens", "strategy"],
  workspaces: ["workspace", "rename", "create workspace"],
  team: ["members", "roles", "invite", "permissions"],
  connectors: ["integrations", "google drive", "github", "figma", "slack", "jira", "confluence", "clickup", "hubspot", "asana", "zoom", "google meet"],
  mcp: ["token", "api", "model context protocol", "access"],
  billing: ["plan", "subscription", "payment", "invoice"],
  security: ["password", "sign out", "sessions"],
  admin: ["api key", "claude key", "owner"],
}

export const STATIC_PAGE_ITEMS: SearchItem[] = [
  {
    id: "action:new-chat",
    group: "actions",
    title: "New chat",
    subtitle: "Start a fresh conversation",
    breadcrumb: [],
    keywords: ["ask", "compose", "start", "conversation"],
    iconId: "chat",
    action: { kind: "new-chat" },
  },
  {
    id: "page:/",
    group: "pages",
    title: "Chat",
    subtitle: "The home surface — ask anything, and your open work tabs",
    breadcrumb: ["Pages"],
    url: "/",
    keywords: ["home", "ask", "workbench", "conversation", "question"],
    iconId: "chat",
    action: { kind: "screen", screen: "chat" },
  },
  {
    id: "page:/brief",
    group: "pages",
    title: "Top Insights",
    subtitle: "This week's findings and insights",
    breadcrumb: ["Pages"],
    url: "/brief",
    keywords: ["brief", "insights", "monday", "report"],
    iconId: "brief",
    action: { kind: "screen", screen: "brief" },
  },
  {
    id: "page:/history",
    group: "pages",
    title: "History",
    subtitle: "All your past chats",
    breadcrumb: ["Pages"],
    url: "/history",
    keywords: ["chats", "conversations", "past", "all chats"],
    iconId: "history",
    action: { kind: "screen", screen: "chats" },
  },
  {
    id: "page:/artifacts",
    group: "pages",
    title: "Artifacts",
    subtitle: "Generated PRDs, prototypes and evidence",
    breadcrumb: ["Pages"],
    url: "/artifacts",
    keywords: ["prd", "prds", "prototypes", "evidence", "generated"],
    iconId: "artifact",
    action: { kind: "screen", screen: "artifacts" },
  },
  // NO "page:" ENTRIES FOR TEMPLATES OR SKILLS. Both moved into Settings, and
  // `buildSettingsItems()` already emits them from SETTINGS_NAV — as
  // `settings:templates` / `settings:skills`, at `/settings?section=…`, with a
  // breadcrumb that says where they actually live. Listing them here too put
  // the same screen in the palette twice under two different paths.
  {
    id: "page:/projects",
    group: "pages",
    title: "Projects",
    subtitle: "Your projects, each with its own chat and tasks",
    breadcrumb: ["Pages"],
    url: "/projects",
    keywords: ["project", "workstream", "tasks", "initiative"],
    iconId: "project",
    action: { kind: "screen", screen: "projects" },
  },
  {
    id: "page:/backlog",
    group: "pages",
    title: "Backlog",
    // Named for what it holds, since "ideation" is the ScreenId and the rail
    // says "Backlog" — someone searching either word should land here.
    subtitle: "The ranked idea list — propose an idea, or generate a PRD from one",
    breadcrumb: ["Pages"],
    url: "/backlog",
    keywords: ["ideas", "ideation", "idea list", "ranked", "proposals", "roadmap"],
    iconId: "backlog",
    action: { kind: "screen", screen: "ideation" },
  },
  {
    id: "page:/sources",
    group: "pages",
    title: "Sources",
    subtitle: "Connected data and uploaded files",
    breadcrumb: ["Pages"],
    url: "/sources",
    keywords: ["files", "data", "uploads", "corpus", "knowledge"],
    iconId: "source",
    action: { kind: "screen", screen: "sources" },
  },
  {
    id: "page:/team",
    group: "pages",
    title: "Team",
    subtitle: "People in this workspace",
    breadcrumb: ["Pages"],
    url: "/team",
    keywords: ["people", "members", "colleagues"],
    iconId: "team",
    action: { kind: "screen", screen: "team" },
  },
  {
    id: "page:/tickets",
    group: "pages",
    title: "Project Management",
    subtitle: "Tickets across your PRDs, and their sync state",
    breadcrumb: ["Pages"],
    url: "/tickets",
    keywords: ["tickets", "jira", "board", "kanban", "issues", "tasks", "sprint"],
    iconId: "tickets",
    action: { kind: "screen", screen: "tickets" },
  },
  {
    id: "page:/roadmap",
    group: "pages",
    title: "Roadmap",
    subtitle: "Your uploaded roadmap document",
    breadcrumb: ["Pages"],
    url: "/roadmap",
    keywords: ["roadmap", "plan", "timeline", "quarters"],
    iconId: "roadmap",
    // A `path`, not a `screen`: /roadmap has no ScreenId — it is a route-only
    // artifact view, so there is nothing for `goTo` to take.
    action: { kind: "path", path: "/roadmap" },
  },
  {
    id: "page:/shipped",
    group: "pages",
    title: "Shipped",
    subtitle: "Work that has already gone out",
    breadcrumb: ["Pages"],
    url: "/shipped",
    keywords: ["shipped", "released", "done", "delivered", "launched"],
    iconId: "shipped",
    action: { kind: "screen", screen: "shipped" },
  },
  {
    id: "page:/past",
    group: "pages",
    title: "Past briefs",
    subtitle: "Earlier weeks of Top Insights",
    breadcrumb: ["Pages"],
    url: "/past",
    keywords: ["past", "history", "previous", "weekly", "archive", "briefs"],
    iconId: "history",
    action: { kind: "screen", screen: "past" },
  },
  {
    id: "page:/evidence",
    group: "pages",
    // SUBTITLE IS A WARNING, NOT A PITCH. Landing here without a finding shows
    // an empty state by design — the screen renders the evidence behind ONE
    // brief finding, and is normally opened from that finding. Saying so beats
    // a result that looks like a library and turns out to be a blank pane.
    title: "Evidence",
    subtitle: "The research behind a brief finding — open one from Top Insights",
    breadcrumb: ["Pages"],
    url: "/evidence",
    keywords: ["evidence", "research", "sources", "citations", "why", "proof"],
    iconId: "doc",
    action: { kind: "screen", screen: "detail" },
  },
  {
    id: "page:/prototype",
    group: "pages",
    // Same warning shape as Evidence: bare `/prototype` prompts you to pick a
    // PRD, because the canvas renders at `/prototype?prd=<id>`.
    title: "Prototype",
    subtitle: "The design canvas — pick a PRD to open its prototype",
    breadcrumb: ["Pages"],
    url: "/prototype",
    keywords: ["prototype", "design", "canvas", "mockup", "figma", "preview"],
    iconId: "prototype",
    action: { kind: "screen", screen: "prototype" },
  },
  {
    id: "page:/settings",
    group: "pages",
    title: "Settings",
    subtitle: "Profile, workspace, integrations and account",
    breadcrumb: ["Pages"],
    url: "/settings",
    keywords: ["preferences", "configuration", "options"],
    iconId: "settings",
    action: { kind: "screen", screen: "settings" },
  },
  // LAST, deliberately. An empty query renders this array in order as a
  // browsable index (CommandPalette's `groups` memo), so position is product:
  // "New chat" leads because it is the primary action, and feedback trails
  // every page because it is a rare one. It is here at all because a stuck
  // user's instinct is to type what they want, and "feedback" matched nothing.
  {
    id: "action:feedback",
    group: "actions",
    title: "Send feedback",
    subtitle: "Tell us what's working and what isn't",
    breadcrumb: [],
    // No `url` — the modal has no route of its own.
    keywords: ["feedback", "bug", "report", "support", "contact", "suggestion", "idea", "complain"],
    iconId: "feedback",
    action: { kind: "feedback" },
  },
]

/**
 * One palette item per settings VIEW — every leaf, not every nav row.
 *
 * The nav consolidated: "Connectors" and "MCP Access" are now views inside a
 * row called "Integrations", and "Metrics" inside one called "Company". Built
 * from rows, this palette would have offered "Integrations" and nothing
 * matching a search for "connectors" — so a person who knows exactly what
 * they want would type its name and be told it does not exist. Search should
 * reach a thing by ITS name, whatever the nav decided to call the drawer it
 * lives in.
 *
 * The breadcrumb carries the pane, so a result still says where it lives:
 * Settings › Data & Integrations › Connectors.
 */
export function buildSettingsItems(): SearchItem[] {
  const items: SearchItem[] = []
  for (const group of SETTINGS_NAV) {
    for (const nav of group.items) {
      if (!nav.available) continue
      // A row that opens somewhere else (Guide → the docs site) is not a
      // `?section=`; it is searchable as itself, at its own href.
      if (nav.href) {
        items.push({
          id: `settings:${nav.id}`,
          group: "settings",
          title: nav.label,
          breadcrumb: ["Settings", group.groupLabel],
          url: nav.href,
          keywords: SETTINGS_KEYWORDS[nav.id] ?? [],
          iconId: "settings",
          action: { kind: "path", path: nav.href },
        })
        continue
      }
      const pane = SETTINGS_PANES.find((p) => p.id === nav.id)
      const leaves = pane ? pane.leaves : [nav.id]
      for (const leaf of leaves) {
        const path = `/settings?section=${leaf}`
        items.push({
          id: `settings:${leaf}`,
          group: "settings",
          // The VIEW's own name — "Connectors", not "Integrations".
          title: LEAF_LABELS[leaf] ?? nav.label,
          breadcrumb: ["Settings", group.groupLabel],
          url: path,
          keywords: SETTINGS_KEYWORDS[leaf] ?? [],
          iconId: "settings",
          action: { kind: "path", path },
        })
      }
    }
  }
  return items
}

/** Everything searchable without a network call. */
export function buildStaticItems(): SearchItem[] {
  return [...STATIC_PAGE_ITEMS, ...buildSettingsItems()]
}
