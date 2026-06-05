/**
 * Catalog shape + content tests for the sprntly_Design-3 reset (commit C).
 * Source of truth: sprntly_Design-3/Sprntly.html lines 2266-2333.
 */
import { describe, expect, it } from "vitest"
import {
  CONNECTOR_CATALOG,
  CONNECTOR_IDS_CONNECTABLE,
  CONNECTOR_IDS_WITH_OAUTH,
} from "../connectorsCatalog"

const EXPECTED_CATEGORIES = [
  "Analytics",
  "Project Management",
  "Customer Voice & Support",
  "Revenue",
  "Code",
  "Monitoring & Reliability",
  "Design",
  "Communication",
] as const

describe("CONNECTOR_CATALOG — design-3 shape", () => {
  it("has exactly the 8 design-3 categories, in order", () => {
    expect(CONNECTOR_CATALOG.map((c) => c.title)).toEqual([...EXPECTED_CATEGORIES])
  })

  it("totals 31 connector rows across all categories (29 design + ClickUp + Fireflies)", () => {
    const total = CONNECTOR_CATALOG.reduce((n, c) => n + c.items.length, 0)
    expect(total).toBe(31)
  })

  it("every category has a non-empty uploadAccept hint + uploadExtensions list", () => {
    for (const cat of CONNECTOR_CATALOG) {
      expect(cat.uploadAccept).toBeTruthy()
      expect(Array.isArray(cat.uploadExtensions)).toBe(true)
      expect(cat.uploadExtensions!.length).toBeGreaterThan(0)
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

  it("Analytics: Mixpanel, Amplitude, Google Analytics, Heap, PostHog", () => {
    expect(items("Analytics")).toEqual([
      "Mixpanel", "Amplitude", "Google Analytics", "Heap", "PostHog",
    ])
  })

  it("Project Management: Linear, Jira, ClickUp, Notion, Google Docs, Asana", () => {
    expect(items("Project Management")).toEqual([
      "Linear", "Jira", "ClickUp", "Notion", "Google Docs", "Asana",
    ])
  })

  it("Customer Voice & Support: Intercom, Zendesk, Fireflies, Gong, Dovetail, Salesforce", () => {
    expect(items("Customer Voice & Support")).toEqual([
      "Intercom", "Zendesk", "Fireflies", "Gong", "Dovetail", "Salesforce",
    ])
  })

  it("Revenue: Stripe, ChartMogul, HubSpot", () => {
    expect(items("Revenue")).toEqual(["Stripe", "ChartMogul", "HubSpot"])
  })

  it("Code: GitHub, GitLab, Bitbucket", () => {
    expect(items("Code")).toEqual(["GitHub", "GitLab", "Bitbucket"])
  })

  it("Monitoring & Reliability: Sentry, Datadog, New Relic, PagerDuty", () => {
    expect(items("Monitoring & Reliability")).toEqual([
      "Sentry", "Datadog", "New Relic", "PagerDuty",
    ])
  })

  it("Design: Figma, Framer", () => {
    expect(items("Design")).toEqual(["Figma", "Framer"])
  })

  it("Communication: Slack, MS Teams", () => {
    expect(items("Communication")).toEqual(["Slack", "MS Teams"])
  })
})

describe("CONNECTOR_IDS_WITH_OAUTH", () => {
  it("contains the connectors with live OAuth backends (Drive/Figma/GitHub + ClickUp + HubSpot + Slack)", () => {
    expect([...CONNECTOR_IDS_WITH_OAUTH].sort()).toEqual(
      ["clickup", "figma", "github", "google_drive", "hubspot", "slack"].sort(),
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
  it("contains all OAuth providers PLUS API-key ones (Fireflies)", () => {
    expect([...CONNECTOR_IDS_CONNECTABLE].sort()).toEqual(
      [
        "clickup",
        "figma",
        "fireflies",
        "github",
        "google_drive",
        "hubspot",
        "slack",
      ].sort(),
    )
  })
})

describe("Google Docs uses the existing google_drive OAuth backend", () => {
  it("the Google Docs row in PM has id 'google_drive' (matches backend provider)", () => {
    const pm = CONNECTOR_CATALOG.find((c) => c.title === "Project Management")!
    const gdocs = pm.items.find((i) => i.name === "Google Docs")
    expect(gdocs?.id).toBe("google_drive")
    expect(gdocs?.oauth).toBe(true)
  })
})
