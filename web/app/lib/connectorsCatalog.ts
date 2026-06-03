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
import type { ConnectorCategoryRow } from "../types/content"

export const CONNECTOR_CATALOG: ConnectorCategoryRow[] = [
  {
    key: "analytics",
    title: "Analytics",
    subLabel: "required",
    uploadAccept: "PDF · CSV · XLSX",
    uploadExtensions: [".pdf", ".csv", ".xlsx"],
    items: [
      { id: "mixpanel",         name: "Mixpanel",         logo: "M", logoText: "M", logoColor: "#7856FF", oauth: false },
      { id: "amplitude",        name: "Amplitude",        logo: "A", logoText: "A", logoColor: "#1A6CFF", oauth: false },
      { id: "google_analytics", name: "Google Analytics", logo: "G", logoText: "G", logoColor: "#F9AB00", oauth: false },
      { id: "heap",             name: "Heap",             logo: "H", logoText: "H", logoColor: "#FF6E6E", oauth: false },
      { id: "posthog",          name: "PostHog",          logo: "P", logoText: "P", logoColor: "#0CC1AE", oauth: false },
    ],
  },
  {
    key: "pm",
    title: "Project Management",
    uploadAccept: "PDF · PPT · DOCX",
    uploadExtensions: [".pdf", ".ppt", ".pptx", ".doc", ".docx"],
    items: [
      { id: "linear",       name: "Linear",      logo: "L", logoText: "L", logoColor: "#5E6AD2", oauth: false },
      { id: "jira",         name: "Jira",        logo: "J", logoText: "J", logoColor: "#0052CC", oauth: false },
      { id: "clickup",      name: "ClickUp",     logo: "C", logoText: "C", logoColor: "#7B68EE", oauth: true },
      { id: "notion",       name: "Notion",      logo: "N", logoText: "N", logoColor: "#000000", oauth: false },
      // Backend provider is `google_drive` (existing OAuth + sync). Surface
      // it as "Google Docs" per design — the connector pulls Google Docs
      // out of Drive folders, so the label matches user expectation.
      { id: "google_drive", name: "Google Docs", logo: "G", logoText: "G", logoColor: "#4285F4", oauth: true },
      { id: "asana",        name: "Asana",       logo: "A", logoText: "A", logoColor: "#F06A6A", oauth: false },
    ],
  },
  {
    key: "voice",
    title: "Customer Voice & Support",
    uploadAccept: "PDF · DOCX · TXT",
    uploadExtensions: [".pdf", ".doc", ".docx", ".txt"],
    items: [
      { id: "intercom",   name: "Intercom",   logo: "I", logoText: "I", logoColor: "#1F8DED", oauth: false },
      { id: "zendesk",    name: "Zendesk",    logo: "Z", logoText: "Z", logoColor: "#03363D", oauth: false },
      { id: "fireflies",  name: "Fireflies",  logo: "F", logoText: "F", logoColor: "#FFAD33", oauth: false, authType: "apikey" },
      { id: "gong",       name: "Gong",       logo: "G", logoText: "G", logoColor: "#E74C3C", oauth: false },
      { id: "dovetail",   name: "Dovetail",   logo: "D", logoText: "D", logoColor: "#9B59B6", oauth: false },
      { id: "salesforce", name: "Salesforce", logo: "S", logoText: "S", logoColor: "#00A1E0", oauth: false },
    ],
  },
  {
    key: "revenue",
    title: "Revenue",
    uploadAccept: "PDF · PPT · XLSX",
    uploadExtensions: [".pdf", ".ppt", ".pptx", ".xlsx"],
    items: [
      { id: "stripe",     name: "Stripe",     logo: "S", logoText: "S", logoColor: "#635BFF", oauth: false },
      { id: "chartmogul", name: "ChartMogul", logo: "C", logoText: "C", logoColor: "#0066FF", oauth: false },
      { id: "hubspot",    name: "HubSpot",    logo: "H", logoText: "H", logoColor: "#FF7A59", oauth: true },
    ],
  },
  {
    key: "code",
    title: "Code",
    uploadAccept: "PDF · MD",
    uploadExtensions: [".pdf", ".md"],
    items: [
      { id: "github",    name: "GitHub",    logo: "G", logoText: "G", logoColor: "#181717", oauth: true },
      { id: "gitlab",    name: "GitLab",    logo: "G", logoText: "G", logoColor: "#FC6D26", oauth: false },
      { id: "bitbucket", name: "Bitbucket", logo: "B", logoText: "B", logoColor: "#205081", oauth: false },
    ],
  },
  {
    key: "monitoring",
    title: "Monitoring & Reliability",
    subLabel: "powers On-Call Agent",
    uploadAccept: "PDF · MD",
    uploadExtensions: [".pdf", ".md"],
    items: [
      { id: "sentry",    name: "Sentry",    logo: "S", logoText: "S", logoColor: "#362D59", oauth: false },
      { id: "datadog",   name: "Datadog",   logo: "D", logoText: "D", logoColor: "#632CA6", oauth: false },
      { id: "newrelic",  name: "New Relic", logo: "N", logoText: "N", logoColor: "#06AC38", oauth: false },
      { id: "pagerduty", name: "PagerDuty", logo: "P", logoText: "P", logoColor: "#06A77D", oauth: false },
    ],
  },
  {
    key: "design",
    title: "Design",
    uploadAccept: "PDF · PNG · JPG",
    uploadExtensions: [".pdf", ".png", ".jpg", ".jpeg"],
    items: [
      { id: "figma",  name: "Figma",  logo: "F", logoText: "F", logoColor: "#F24E1E", oauth: true },
      { id: "framer", name: "Framer", logo: "F", logoText: "F", logoColor: "#000000", oauth: false },
    ],
  },
  {
    key: "comms",
    title: "Communication",
    uploadAccept: "PDF · DOCX · TXT",
    uploadExtensions: [".pdf", ".doc", ".docx", ".txt"],
    items: [
      { id: "slack",   name: "Slack",    logo: "S", logoText: "S", logoColor: "#4A154B", oauth: false },
      { id: "msteams", name: "MS Teams", logo: "M", logoText: "M", logoColor: "#5059C9", oauth: false },
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
