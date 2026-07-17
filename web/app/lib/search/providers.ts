import {
  askApi,
  artifactsApi,
  companyDocsApi,
  connectorsApi,
  conversationsApi,
  teamApi,
  templatesApi,
  type ArtifactItem,
  type CompanyDocument,
  type CompanyTemplate,
  type ConnectionSummary,
  type ConversationRecord,
  type SkillInfo,
  type TeamMemberRecord,
} from "../api"
import { prototypePath } from "../routes"
import type { SearchItem } from "./types"

// ── Dynamic search providers: workspace entities, fetched lazily ─────────────
//
// The palette fans out to the existing per-domain list endpoints the first
// time it opens (all workspace-scoped automatically via the X-Workspace-Id
// header in api.ts) and caches the PROMISE per workspace, so concurrent opens
// dedupe and re-opens are instant. One failing endpoint never blanks the
// others (allSettled). Switching workspace hits a different cache key.

/** Cap noisy collections so one domain can't drown the index. */
const MAX_CHATS = 200

const cache = new Map<string, Promise<SearchItem[]>>()

/** Drop all cached results (tests; explicit refresh). */
export function invalidateSearchCache(): void {
  cache.clear()
}

export type ProviderDeps = {
  /** Dataset slug from CompanyContext.activeCompany — artifacts need it. */
  activeCompany: string | null
}

export function fetchDynamicItems(
  workspaceId: string,
  deps: ProviderDeps,
): Promise<SearchItem[]> {
  const key = `${workspaceId}:${deps.activeCompany ?? ""}`
  const hit = cache.get(key)
  if (hit) return hit
  const promise = loadAll(deps)
  cache.set(key, promise)
  return promise
}

async function loadAll(deps: ProviderDeps): Promise<SearchItem[]> {
  const settled = await Promise.allSettled([
    askApi.skills().then((r) => r.skills.map(skillItem)),
    conversationsApi
      .list()
      .then((r) => r.conversations.slice(0, MAX_CHATS).map(chatItem)),
    deps.activeCompany
      ? artifactsApi
          .list(deps.activeCompany)
          .then((rows) => rows.map(artifactItem).filter((x): x is SearchItem => x !== null))
      : Promise.resolve([]),
    companyDocsApi.list().then((docs) => docs.map(documentItem)),
    templatesApi.list().then((tpls) => tpls.map(templateItem)),
    teamApi.list().then((r) => r.members.map(teamItem)),
    connectorsApi.list().then((r) => r.connections.map(connectorItem)),
  ])
  return settled.flatMap((s) => (s.status === "fulfilled" ? s.value : []))
}

// ── Mappers ──────────────────────────────────────────────────────────────────

function skillItem(s: SkillInfo): SearchItem {
  return {
    id: `skill:${s.id}`,
    group: "skills",
    title: s.label,
    subtitle: s.description,
    breadcrumb: ["Skills", s.category],
    url: "/skills",
    keywords: [s.trigger, s.category],
    iconId: "skill",
    // Deep-link into the skills page pre-filtered to this skill.
    action: { kind: "path", path: `/skills?q=${encodeURIComponent(s.label)}` },
  }
}

function chatItem(c: ConversationRecord): SearchItem {
  return {
    id: `chat:${c.id}`,
    group: "chats",
    title: c.title || "Untitled chat",
    subtitle: c.preview || undefined,
    breadcrumb: ["History"],
    keywords: [],
    iconId: "chat",
    action: { kind: "resume-chat", dbId: c.id, title: c.title || "Untitled chat" },
  }
}

function artifactItem(a: ArtifactItem): SearchItem | null {
  if (a.type === "prd") {
    return {
      id: `artifact:prd:${a.id}`,
      group: "artifacts",
      title: a.title,
      subtitle: a.source.week_label ? `PRD · ${a.source.week_label}` : "PRD",
      breadcrumb: ["Artifacts", "PRDs"],
      keywords: ["prd"],
      iconId: "artifact",
      action: {
        kind: "prd-tab",
        prdId: a.open.prd_id,
        title: `PRD · ${a.title}`,
        briefId: a.open.brief_id,
        insightIndex: a.open.insight_index ?? 0,
      },
    }
  }
  if (a.type === "prototype") {
    const path = prototypePath(a.open.prd_id)
    return {
      id: `artifact:proto:${a.id}`,
      group: "artifacts",
      title: a.title,
      subtitle: "Prototype",
      breadcrumb: ["Artifacts", "Prototypes"],
      url: path,
      keywords: ["prototype", "canvas", "design"],
      iconId: "prototype",
      action: { kind: "path", path },
    }
  }
  // Evidence opens through a content-panel load that isn't reachable from a
  // serializable action yet — the Artifacts page remains its front door.
  return null
}

function documentItem(d: CompanyDocument): SearchItem {
  return {
    id: `doc:${d.id}`,
    group: "documents",
    title: d.filename,
    subtitle: d.doc_type.replace(/_/g, " "),
    breadcrumb: ["Sources", "Documents"],
    url: "/sources",
    keywords: [d.doc_type.replace(/_/g, " ")],
    iconId: "doc",
    action: { kind: "screen", screen: "sources" },
  }
}

function templateItem(t: CompanyTemplate): SearchItem {
  return {
    id: `template:${t.id}`,
    group: "documents",
    title: t.label ?? t.filename,
    subtitle: "Template",
    breadcrumb: ["Templates"],
    url: "/templates",
    keywords: ["template", t.type],
    iconId: "template",
    action: { kind: "screen", screen: "templates" },
  }
}

function teamItem(m: TeamMemberRecord): SearchItem {
  const name = m.display_name || m.email || "Member"
  return {
    id: `member:${m.user_id}`,
    group: "team",
    title: name,
    subtitle: m.email && m.email !== name ? m.email : undefined,
    breadcrumb: ["Settings", "Team & roles"],
    url: "/settings?section=team",
    keywords: [m.email ?? "", m.role].filter(Boolean),
    iconId: "person",
    action: { kind: "path", path: "/settings?section=team" },
  }
}

/** "google_drive" → "Google Drive". */
export function connectorDisplayName(provider: string): string {
  if (provider === "github") return "GitHub"
  if (provider === "hubspot") return "HubSpot"
  if (provider === "clickup") return "ClickUp"
  return provider
    .split(/[_-]/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ")
}

function connectorItem(c: ConnectionSummary): SearchItem {
  return {
    id: `connector:${c.id}`,
    group: "connectors",
    title: connectorDisplayName(c.provider),
    subtitle: c.google_email ?? c.account_label ?? undefined,
    breadcrumb: ["Settings", "Connectors"],
    url: "/settings?section=connectors",
    keywords: ["connector", "integration", c.provider.replace(/_/g, " ")],
    iconId: "connector",
    action: { kind: "path", path: "/settings?section=connectors" },
  }
}
