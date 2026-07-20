import {
  SETTINGS_NAV,
  type SettingsSectionId,
} from "../../components/screens/app/settings/SettingsLayout"
import type { SearchItem } from "./types"

// ── Static searchable surface: pages, settings panes, actions ────────────────
//
// Pages mirror the sidebar's nav surfaces (SCREEN_PATH in lib/routes.ts);
// dormant routes that were removed from the nav (backlog, tickets, past, …)
// are deliberately absent — surfacing them in search would resurrect surfaces
// the design reset retired. Settings items DERIVE from SETTINGS_NAV so the
// palette can never drift from what the settings sidebar actually shows
// (including the everyone-sees-admin choice documented there).

/** Extra matchable aliases per settings pane, beyond its nav label. */
const SETTINGS_KEYWORDS: Partial<Record<SettingsSectionId, string[]>> = {
  profile: ["account", "name", "avatar", "role"],
  "comms-brief": ["email", "notifications", "weekly brief", "slack"],
  "product-category": ["product", "category"],
  "company-profile": ["mission", "icp", "tone", "voice", "company"],
  process: ["planning", "sprint", "cadence"],
  metrics: ["kpi", "kpis", "definitions", "measures"],
  "business-context": ["context", "lens", "strategy"],
  workspaces: ["workspace", "rename", "create workspace"],
  team: ["members", "roles", "invite", "permissions"],
  connectors: ["integrations", "google drive", "github", "figma", "slack", "jira", "clickup", "hubspot", "asana"],
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
    id: "page:/brief",
    group: "pages",
    title: "Weekly brief",
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
  {
    id: "page:/templates",
    group: "pages",
    title: "Templates",
    subtitle: "Gold-standard examples",
    breadcrumb: ["Pages"],
    url: "/templates",
    keywords: ["gold standard", "examples", "prd template"],
    iconId: "template",
    action: { kind: "screen", screen: "templates" },
  },
  {
    id: "page:/skills",
    group: "pages",
    title: "Skills",
    subtitle: "PM workflows the chat can run",
    breadcrumb: ["Pages"],
    url: "/skills",
    keywords: ["workflows", "commands", "abilities"],
    iconId: "skill",
    action: { kind: "screen", screen: "skills" },
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
]

/** One palette item per visible settings pane, derived from SETTINGS_NAV. */
export function buildSettingsItems(): SearchItem[] {
  const items: SearchItem[] = []
  for (const group of SETTINGS_NAV) {
    for (const nav of group.items) {
      if (!nav.available) continue
      const path = `/settings?section=${nav.id}`
      items.push({
        id: `settings:${nav.id}`,
        group: "settings",
        title: nav.label,
        breadcrumb: ["Settings", group.groupLabel],
        url: path,
        keywords: SETTINGS_KEYWORDS[nav.id] ?? [],
        iconId: "settings",
        action: { kind: "path", path },
      })
    }
  }
  return items
}

/** Everything searchable without a network call. */
export function buildStaticItems(): SearchItem[] {
  return [...STATIC_PAGE_ITEMS, ...buildSettingsItems()]
}
