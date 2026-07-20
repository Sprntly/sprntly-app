/**
 * Catalog shape + content tests — onboarding v6 (screenshot spec 2026-07-17)
 * category order, with the settings-only extras (docs, revenue) appended.
 */
import { describe, expect, it } from "vitest"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_CONNECTABLE,
  CONNECTOR_IDS_WITH_OAUTH,
  connectableCatalog,
  isConnectableConnector,
} from "../connectorsCatalog"

const EXPECTED_CATEGORIES = [
  "Analytics",
  "Voice of Customer & Support",
  "Customer Relationship (CRM)",
  "Project Management",
  "Monitoring & Reliability",
  "Design",
  "Codebase",
  "Communications",
  "Business documentation",
  "Revenue",
] as const

describe("CONNECTOR_CATALOG — design-3 shape", () => {
  it("has exactly the 10 categories, in v6 order (CRM added; docs + revenue appended)", () => {
    expect(CONNECTOR_CATALOG.map((c) => c.title)).toEqual([...EXPECTED_CATEGORIES])
  })

  it("totals 40 connector rows across all categories (v6: + Segment, App/Play Store, CRM roster)", () => {
    const total = CONNECTOR_CATALOG.reduce((n, c) => n + c.items.length, 0)
    expect(total).toBe(40)
  })

  it("every category has a non-empty uploadAccept hint + uploadExtensions list", () => {
    for (const cat of CONNECTOR_CATALOG) {
      expect(cat.uploadAccept).toBeTruthy()
      expect(Array.isArray(cat.uploadExtensions)).toBe(true)
      expect(cat.uploadExtensions!.length).toBeGreaterThan(0)
    }
  })

  it("every category accepts Word files (.doc + .docx)", () => {
    for (const cat of CONNECTOR_CATALOG) {
      expect(cat.uploadExtensions).toContain(".doc")
      expect(cat.uploadExtensions).toContain(".docx")
    }
  })

  it("every item has a single-letter logoText and a hex logoColor", () => {
    for (const cat of CONNECTOR_CATALOG) {
      for (const item of cat.items) {
        expect(item.logoText?.length).toBe(1)
        expect(item.logoColor).toMatch(/^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/)
      }
    }
  })
})

describe("CONNECTOR_CATALOG — category sub-labels", () => {
  it("Analytics is labelled 'required'", () => {
    const analytics = CONNECTOR_CATALOG.find((c) => c.title === "Analytics")!
    expect(analytics.subLabel).toBe("required")
  })

  it("Monitoring & Reliability is labelled 'powers On-Call Agent'", () => {
    const monitoring = CONNECTOR_CATALOG.find(
      (c) => c.title === "Monitoring & Reliability",
    )!
    expect(monitoring.subLabel).toBe("powers On-Call Agent")
  })

  it("other categories have no sub-label", () => {
    const others = CONNECTOR_CATALOG.filter(
      (c) => c.title !== "Analytics" && c.title !== "Monitoring & Reliability",
    )
    for (const c of others) {
      expect(c.subLabel).toBeUndefined()
    }
  })
})

describe("CONNECTOR_CATALOG — connector inventory per category", () => {
  function items(title: string): string[] {
    const cat = CONNECTOR_CATALOG.find((c) => c.title === title)
    if (!cat) throw new Error(`Missing category: ${title}`)
    return cat.items.map((i) => i.name)
  }

  it("Analytics: Mixpanel, Amplitude, Google Analytics, Heap, PostHog, Segment, Superset", () => {
    expect(items("Analytics")).toEqual([
      "Mixpanel", "Amplitude", "Google Analytics", "Heap", "PostHog", "Segment",
      "Superset",
    ])
  })

  it("Project Management: Linear, Jira, ClickUp, Asana (Notion + Google Docs moved out)", () => {
    expect(items("Project Management")).toEqual([
      "Linear", "Jira", "ClickUp", "Asana",
    ])
  })

  it("Business documentation: Notion, Google Docs", () => {
    expect(items("Business documentation")).toEqual(["Notion", "Google Docs"])
  })

  it("Voice of Customer & Support: Zendesk, Intercom, Dovetail, App Store, Play Store, Sprinklr, Fireflies, Gong", () => {
    expect(items("Voice of Customer & Support")).toEqual([
      "Zendesk", "Intercom", "Dovetail", "App Store", "Play Store", "Sprinklr",
      "Fireflies", "Gong",
    ])
  })

  it("Customer Relationship (CRM): HubSpot, Salesforce, Pipedrive, Attio, Close, Zoho CRM", () => {
    expect(items("Customer Relationship (CRM)")).toEqual([
      "HubSpot", "Salesforce", "Pipedrive", "Attio", "Close", "Zoho CRM",
    ])
  })

  it("Revenue: Stripe, ChartMogul (HubSpot moved to CRM)", () => {
    expect(items("Revenue")).toEqual(["Stripe", "ChartMogul"])
  })

  it("Codebase: GitHub, GitLab, Bitbucket", () => {
    expect(items("Codebase")).toEqual(["GitHub", "GitLab", "Bitbucket"])
  })

  it("Monitoring & Reliability: Sentry, Datadog, New Relic, PagerDuty", () => {
    expect(items("Monitoring & Reliability")).toEqual([
      "Sentry", "Datadog", "New Relic", "PagerDuty",
    ])
  })

  it("Design: Figma, Framer", () => {
    expect(items("Design")).toEqual(["Figma", "Framer"])
  })

  it("Communications: Slack, MS Teams", () => {
    expect(items("Communications")).toEqual(["Slack", "MS Teams"])
  })
})

describe("CONNECTOR_IDS_WITH_OAUTH", () => {
  it("contains the connectors whose UI surfaces a live OAuth flow", () => {
    // Figma is OAuth-only for the app-review resubmission — Figma's reviewers
    // rejected the PAT-based connect path, so it was removed entirely (no
    // figma_pat module, no /figma/pat route).
    expect([...CONNECTOR_IDS_WITH_OAUTH].sort()).toEqual(
      [
        "asana", "clickup", "figma", "github", "google_drive",
        "hubspot", "jira", "slack", "sprinklr",
      ].sort(),
    )
  })

  it("is derived from the catalog (oauth flag) — they stay in sync", () => {
    const flaggedOauth = CONNECTOR_CATALOG.flatMap((c) => c.items)
      .filter((i) => i.oauth)
      .map((i) => i.id)
    expect(flaggedOauth.sort()).toEqual([...CONNECTOR_IDS_WITH_OAUTH].sort())
  })

  it("excludes Fireflies (it's API-key based, not OAuth)", () => {
    expect(CONNECTOR_IDS_WITH_OAUTH.has("fireflies")).toBe(false)
  })
})

describe("CONNECTOR_IDS_CONNECTABLE", () => {
  it("contains all OAuth providers PLUS API-key (Fireflies) and credentials (Superset) ones", () => {
    expect([...CONNECTOR_IDS_CONNECTABLE].sort()).toEqual(
      [
        "asana",
        "clickup",
        "figma",
        "fireflies",
        "github",
        "google_drive",
        "hubspot",
        "jira",
        "slack",
        "sprinklr",
        "superset",
      ].sort(),
    )
  })
})

describe("Google Docs uses the existing google_drive OAuth backend", () => {
  it("the Google Docs row in Business documentation has id 'google_drive' (matches backend provider)", () => {
    const docs = CONNECTOR_CATALOG.find((c) => c.title === "Business documentation")!
    const gdocs = docs.items.find((i) => i.name === "Google Docs")
    expect(gdocs?.id).toBe("google_drive")
    expect(gdocs?.oauth).toBe(true)
  })
})

describe("Business documentation category", () => {
  it("contains Notion + Google Docs and they no longer sit under Project Management", () => {
    const docs = CONNECTOR_CATALOG.find((c) => c.title === "Business documentation")!
    expect(docs.items.map((i) => i.id)).toEqual(["notion", "google_drive"])
    const pm = CONNECTOR_CATALOG.find((c) => c.title === "Project Management")!
    const pmIds = pm.items.map((i) => i.id)
    expect(pmIds).not.toContain("notion")
    expect(pmIds).not.toContain("google_drive")
  })
})

describe("connectableCatalog — Settings tab (hide 'Coming soon')", () => {
  it("keeps only the categories that still have a wired connector, in order", () => {
    expect(connectableCatalog().map((c) => c.title)).toEqual([
      "Analytics",
      "Voice of Customer & Support",
      "Customer Relationship (CRM)",
      "Project Management",
      "Design",
      "Codebase",
      "Communications",
      "Business documentation",
    ])
  })

  it("shows only the 11 wired connectors (OAuth + API key + credentials) and nothing else", () => {
    const ids = connectableCatalog()
      .flatMap((c) => c.items)
      .map((i) => i.id)
      .sort()
    expect(ids).toEqual(
      [
        "asana",
        "clickup",
        "figma",
        "fireflies",
        "github",
        "google_drive",
        "hubspot",
        "jira",
        "slack",
        "sprinklr",
        "superset",
      ].sort(),
    )
  })

  it("drops categories that end up with no connectors (Monitoring, Revenue)", () => {
    const titles = connectableCatalog().map((c) => c.title)
    expect(titles).not.toContain("Monitoring & Reliability")
    expect(titles).not.toContain("Revenue")
    const byTitle = (t: string) =>
      connectableCatalog().find((c) => c.title === t)!.items.map((i) => i.id)
    expect(byTitle("Analytics")).toEqual(["superset"])
    expect(byTitle("Voice of Customer & Support")).toEqual(["sprinklr", "fireflies"])
    expect(byTitle("Customer Relationship (CRM)")).toEqual(["hubspot"])
    expect(byTitle("Project Management")).toEqual(["jira", "clickup", "asana"])
    expect(byTitle("Business documentation")).toEqual(["google_drive"])
    expect(byTitle("Codebase")).toEqual(["github"])
    expect(byTitle("Communications")).toEqual(["slack"])
  })

  it("preserves each category's upload strip metadata (uploads still work when empty)", () => {
    for (const cat of connectableCatalog()) {
      expect(cat.uploadAccept).toBeTruthy()
      expect(cat.uploadExtensions!.length).toBeGreaterThan(0)
    }
  })

  it("never hides a provider that has a live connection, even if not yet wired", () => {
    const cats = connectableCatalog(new Set(["mixpanel"]))
    const analytics = cats.find((c) => c.title === "Analytics")!
    // Live-but-unwired Mixpanel joins the wired Superset, catalog order.
    expect(analytics.items.map((i) => i.id)).toEqual(["mixpanel", "superset"])
  })

  it("does not mutate the source CONNECTOR_CATALOG", () => {
    const before = CONNECTOR_CATALOG.flatMap((c) => c.items).length
    connectableCatalog()
    expect(CONNECTOR_CATALOG.flatMap((c) => c.items).length).toBe(before)
  })
})

describe("isConnectableConnector", () => {
  it("true for OAuth, API-key, and credentials connectors, false for 'Coming soon'", () => {
    expect(isConnectableConnector({ id: "slack", name: "Slack", logo: "S", oauth: true })).toBe(true)
    expect(
      isConnectableConnector({ id: "fireflies", name: "Fireflies", logo: "F", oauth: false, authType: "apikey" }),
    ).toBe(true)
    expect(
      isConnectableConnector({ id: "superset", name: "Superset", logo: "S", oauth: false, authType: "credentials" }),
    ).toBe(true)
    expect(isConnectableConnector({ id: "mixpanel", name: "Mixpanel", logo: "M", oauth: false })).toBe(false)
  })
})
