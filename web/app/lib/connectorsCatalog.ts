/**
 * Connector catalog for the Settings → Connectors pane.
 *
 * Source of truth for category order, connector inventory, single-letter
 * logos, and brand colors is sprntly_Design-3/Sprntly.html (lines 2266-
 * 2333). When a connector has a real OAuth backend, `oauth: true` — the
 * UI enables its `Connect` button; everything else renders disabled with
 * a "Coming soon" tooltip per SETTINGS_PAGE_PLAN.md §7 decision 4.
 *
 * Connection state is fetched separately from `GET /v1/connectors`.
 */
import type { ConnectorCategoryRow, ConnectorItemRow, ConnectorType } from "../types/content"
import { UPLOAD_ACCEPT_HINT, UPLOAD_EXTENSIONS } from "./sources-helpers"

export const CONNECTOR_CATALOG: ConnectorCategoryRow[] = [
  {
    key: "analytics",
    title: "Analytics",
    subLabel: "required",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "mixpanel",         name: "Mixpanel",         logo: "M", logoText: "M", logoColor: "#7856FF", oauth: false, types: ["analytics"] },
      { id: "amplitude",        name: "Amplitude",        logo: "A", logoText: "A", logoColor: "#1A6CFF", logoSvg: "/connectors/amplitude.svg", oauth: false, types: ["analytics"] },
      { id: "google_analytics", name: "Google Analytics", logo: "G", logoText: "G", logoColor: "#F9AB00", logoSvg: "/connectors/google_analytics.svg", oauth: false, types: ["analytics"] },
      { id: "heap",             name: "Heap",             logo: "H", logoText: "H", logoColor: "#FF6E6E", oauth: false, types: ["analytics"] },
      { id: "posthog",          name: "PostHog",          logo: "P", logoText: "P", logoColor: "#0CC1AE", logoSvg: "/connectors/posthog.svg", oauth: false, types: ["analytics"] },
    ],
  },
  {
    key: "pm",
    title: "Project Management",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "linear",       name: "Linear",      logo: "L", logoText: "L", logoColor: "#5E6AD2", logoSvg: "/connectors/linear.svg", oauth: false, types: ["task-management"] },
      { id: "jira",         name: "Jira",        logo: "J", logoText: "J", logoColor: "#0052CC", logoSvg: "/connectors/jira.svg", oauth: true, types: ["task-management"] },
      { id: "clickup",      name: "ClickUp",     logo: "C", logoText: "C", logoColor: "#7B68EE", logoSvg: "/connectors/clickup.svg", oauth: true, types: ["task-management"] },
      { id: "asana",        name: "Asana",       logo: "A", logoText: "A", logoColor: "#F06A6A", logoSvg: "/connectors/asana.svg", oauth: false, types: ["task-management"] },
    ],
  },
  {
    // Notion and Google Docs are documentation tools, not project trackers —
    // they were previously miscategorized under "Project Management".
    key: "docs",
    title: "Business documentation",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "notion",       name: "Notion",      logo: "N", logoText: "N", logoColor: "#000000", logoSvg: "/connectors/notion.svg", oauth: false, types: ["documents"] },
      // Backend provider is `google_drive` (existing OAuth + sync). Surface
      // it as "Google Docs" per design — the connector pulls Google Docs
      // out of Drive folders, so the label matches user expectation.
      { id: "google_drive", name: "Google Docs", logo: "G", logoText: "G", logoColor: "#4285F4", logoSvg: "/connectors/google_drive.svg", oauth: true, types: ["documents"] },
    ],
  },
  {
    key: "voice",
    title: "Customer Voice & Support",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "intercom",   name: "Intercom",   logo: "I", logoText: "I", logoColor: "#1F8DED", logoSvg: "/connectors/intercom.svg", oauth: false, types: ["communication"] },
      { id: "zendesk",    name: "Zendesk",    logo: "Z", logoText: "Z", logoColor: "#03363D", logoSvg: "/connectors/zendesk.svg", oauth: false, types: ["customer-voice"] },
      // Fireflies has no official SVG mark we could bundle, so it keeps the
      // brand-color letter glyph (sharper than the old fuzzy favicon anyway).
      { id: "fireflies",  name: "Fireflies",  logo: "F", logoText: "F", logoColor: "#FFAD33", oauth: false, authType: "apikey", types: ["meetings"] },
      { id: "gong",       name: "Gong",       logo: "G", logoText: "G", logoColor: "#E74C3C", oauth: false, types: ["meetings"] },
      { id: "dovetail",   name: "Dovetail",   logo: "D", logoText: "D", logoColor: "#9B59B6", oauth: false, types: ["customer-voice"] },
      { id: "salesforce", name: "Salesforce", logo: "S", logoText: "S", logoColor: "#00A1E0", logoSvg: "/connectors/salesforce.svg", oauth: false, types: ["crm"] },
    ],
  },
  {
    key: "revenue",
    title: "Revenue",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "stripe",     name: "Stripe",     logo: "S", logoText: "S", logoColor: "#635BFF", logoSvg: "/connectors/stripe.svg", oauth: false, types: ["revenue"] },
      { id: "chartmogul", name: "ChartMogul", logo: "C", logoText: "C", logoColor: "#0066FF", oauth: false, types: ["revenue"] },
      { id: "hubspot",    name: "HubSpot",    logo: "H", logoText: "H", logoColor: "#FF7A59", logoSvg: "/connectors/hubspot.svg", oauth: true, types: ["crm"] },
    ],
  },
  {
    key: "code",
    title: "Code",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "github",    name: "GitHub",    logo: "G", logoText: "G", logoColor: "#181717", logoSvg: "/connectors/github.svg", oauth: true, types: ["code"] },
      { id: "gitlab",    name: "GitLab",    logo: "G", logoText: "G", logoColor: "#FC6D26", logoSvg: "/connectors/gitlab.svg", oauth: false, types: ["code"] },
      { id: "bitbucket", name: "Bitbucket", logo: "B", logoText: "B", logoColor: "#205081", logoSvg: "/connectors/bitbucket.svg", oauth: false, types: ["code"] },
    ],
  },
  {
    key: "monitoring",
    title: "Monitoring & Reliability",
    subLabel: "powers On-Call Agent",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      { id: "sentry",    name: "Sentry",    logo: "S", logoText: "S", logoColor: "#362D59", logoSvg: "/connectors/sentry.svg", oauth: false, types: ["monitoring"] },
      { id: "datadog",   name: "Datadog",   logo: "D", logoText: "D", logoColor: "#632CA6", logoSvg: "/connectors/datadog.svg", oauth: false, types: ["monitoring"] },
      { id: "newrelic",  name: "New Relic", logo: "N", logoText: "N", logoColor: "#06AC38", oauth: false, types: ["monitoring"] },
      { id: "pagerduty", name: "PagerDuty", logo: "P", logoText: "P", logoColor: "#06A77D", logoSvg: "/connectors/pagerduty.svg", oauth: false, types: ["monitoring"] },
    ],
  },
  {
    key: "design",
    title: "Design",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      // Figma is OAuth-only. The legacy PAT connect path was removed entirely
      // (no figma_pat module, no /figma/pat route) — Figma's app review requires
      // OAuth as the sole public connect mechanism.
      { id: "figma",  name: "Figma",  logo: "F", logoText: "F", logoColor: "#F24E1E", logoSvg: "/connectors/figma.svg", oauth: true, types: ["design"] },
      { id: "framer", name: "Framer", logo: "F", logoText: "F", logoColor: "#000000", logoSvg: "/connectors/framer.svg", oauth: false, types: ["design"] },
    ],
  },
  {
    key: "comms",
    title: "Communication",
    uploadAccept: UPLOAD_ACCEPT_HINT,
    uploadExtensions: UPLOAD_EXTENSIONS,
    items: [
      // OAuth-only: Connect routes through Slack's OAuth "Add to Slack" flow
      // (Slack Marketplace requires OAuth install, not a pasted bot token).
      { id: "slack",   name: "Slack",    logo: "S", logoText: "S", logoColor: "#4A154B", logoSvg: "/connectors/slack.svg", oauth: true, types: ["communication"] },
      { id: "msteams", name: "MS Teams", logo: "M", logoText: "M", logoColor: "#5059C9", logoSvg: "/connectors/msteams.svg", oauth: false, types: ["communication"] },
    ],
  },
]

/**
 * Convenience set of connector IDs that have a real OAuth backend.
 * Derived from the `oauth` flag on each catalog row so the two stay in
 * sync automatically — never hand-edit this directly.
 *
 * Note: This is "has OAuth specifically." For "is connectable by any
 * auth mechanism" (OAuth or API key), use CONNECTOR_IDS_CONNECTABLE.
 */
export const CONNECTOR_IDS_WITH_OAUTH = new Set<string>(
  CONNECTOR_CATALOG.flatMap((c) => c.items)
    .filter((i) => i.oauth)
    .map((i) => i.id),
)

/**
 * All connectors the UI should expose as clickable Connect (whether the
 * underlying auth is OAuth or API key). Derived — keep in sync via the
 * `oauth` flag or `authType: "apikey"` per row, not by hand-editing.
 */
export const CONNECTOR_IDS_CONNECTABLE = new Set<string>(
  CONNECTOR_CATALOG.flatMap((c) => c.items)
    .filter((i) => i.oauth || i.authType === "apikey")
    .map((i) => i.id),
)

/** True iff this connector has a working integration the user can actually
 *  use today (OAuth or API key). Everything else is "Coming soon". */
export function isConnectableConnector(item: ConnectorItemRow): boolean {
  return Boolean(item.oauth) || item.authType === "apikey"
}

/**
 * The catalog as shown in Settings → Connectors: drop "Coming soon" connectors
 * (no working integration) so we don't surface things the user can't use, and
 * drop any category that ends up with no connectors so we don't show an empty
 * section. (Uploads aren't lost — they're stored company-wide, and every
 * remaining category still has its file-upload strip.)
 *
 * Providers in `alsoKeepIds` — e.g. any with a live connection — are never
 * hidden even if not yet OAuth/API-key wired; a category kept alive by such a
 * provider is therefore retained too.
 */
export function connectableCatalog(
  alsoKeepIds: ReadonlySet<string> = new Set(),
): ConnectorCategoryRow[] {
  return CONNECTOR_CATALOG.map((cat) => ({
    ...cat,
    items: cat.items.filter(
      (i) => isConnectableConnector(i) || alsoKeepIds.has(i.id),
    ),
  })).filter((cat) => cat.items.length > 0)
}

// ── Connector types ──────────────────────────────────────────────────────────
//
// Every catalog item carries its type (what the tool IS), the mirror of the
// backend authority (backend/app/connectors/catalog.py). Features read these
// instead of hardcoding provider ids. ONE type per connector for now (product
// decision) — the list shape is future-proofing for multi-type.

/** Human labels for the type chips shown on connector cards. */
export const CONNECTOR_TYPE_LABELS: Record<ConnectorType, string> = {
  "task-management": "Task management",
  communication: "Communication",
  documents: "Documents",
  "customer-voice": "Customer voice",
  meetings: "Meetings",
  analytics: "Analytics",
  revenue: "Revenue",
  crm: "CRM",
  code: "Code",
  monitoring: "Monitoring",
  design: "Design",
}

const ALL_ITEMS: ConnectorItemRow[] = CONNECTOR_CATALOG.flatMap((c) => c.items)

/** A connector's types ([] for unknown ids — never throws). */
export function connectorTypes(id: string): ConnectorType[] {
  return ALL_ITEMS.find((i) => i.id === id)?.types ?? []
}

/** Every catalog connector carrying `type` (e.g. all task-management tools). */
export function connectorsWithType(type: ConnectorType): ConnectorItemRow[] {
  return ALL_ITEMS.filter((i) => (i.types ?? []).includes(type))
}

/**
 * Providers the backend's ticket-sync engine implements (mirror of
 * app/stories/sync.py SYNC_PROVIDERS). A connector must be typed
 * `task-management` AND be in this set to appear on the sync button — the
 * type declares what a tool is, the engine declares what we can do with it.
 */
export const TICKET_SYNC_IMPLEMENTED = new Set<string>(["clickup", "jira"])

/** The task-management tools tickets can actually sync with: {id, label} for
 *  the sync button, its tool menu, and its labels. */
export function ticketSyncTrackers(): { id: string; label: string }[] {
  return connectorsWithType("task-management")
    .filter((i) => TICKET_SYNC_IMPLEMENTED.has(i.id))
    .map((i) => ({ id: i.id, label: i.name }))
}
