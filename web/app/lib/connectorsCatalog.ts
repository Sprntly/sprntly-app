import type { ConnectorCategoryRow } from "../types/content"

/** Static catalog; connection state comes from GET /v1/connectors. */
export const CONNECTOR_CATALOG: ConnectorCategoryRow[] = [
  {
    key: "analytics",
    title: "Product analytics",
    subtitle:
      "Cohorts, funnels, and event data — ground findings in real user behavior.",
    items: [
      { id: "amplitude", logo: "Am", name: "Amplitude" },
      { id: "mixpanel", logo: "Mx", name: "Mixpanel" },
      { id: "posthog", logo: "PH", name: "PostHog" },
      { id: "ga4", logo: "G4", name: "GA4" },
    ],
  },
  {
    key: "pm",
    title: "Project management & docs",
    subtitle: "PRDs, specs, and roadmaps Sprntly can read when generating briefs.",
    items: [
      { id: "notion", logo: "No", name: "Notion" },
      { id: "google_drive", logo: "GD", name: "Google Drive" },
      { id: "linear", logo: "Li", name: "Linear" },
      { id: "jira", logo: "Ji", name: "Jira" },
    ],
  },
  {
    key: "feedback",
    title: "Customer feedback",
    subtitle: "Support, NPS, and feature requests in users' own words.",
    items: [
      { id: "intercom", logo: "In", name: "Intercom" },
      { id: "zendesk", logo: "Zn", name: "Zendesk" },
    ],
  },
]

export const CONNECTOR_IDS_WITH_OAUTH = new Set(["google_drive"])
