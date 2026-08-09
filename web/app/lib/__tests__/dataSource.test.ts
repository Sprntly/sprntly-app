// @vitest-environment node
//
// "Data source" definition (boss's rule, 2026-07): a connector counts as a data
// source — and can drive the brief — iff its CATEGORY is evidence-bearing, i.e.
// NOT one of {pm, code, design, docs}. So analytics, customer voice
// (support/calls/feedback), CRM, revenue, and monitoring count;
// Jira & other PM tools, GitHub, Figma, and docs tools
// (Notion / Google Docs) do NOT. Slack DOES count since 2026-07-30: it is
// dual-typed communication + customer-voice and listed on the Voice shelf
// (its synced channels are evidence).
//
// `comms` left the deny-list on 2026-08-04 when the Communications category
// was removed from the catalog. Nothing changed about what counts: MS Teams
// went with the category, and an unknown provider id is not evidence either
// way (see the msteams case below, which still returns false).
import { describe, it, expect } from "vitest"
import {
  CONNECTOR_CATALOG,
  isEvidenceConnector,
  hasEvidenceConnector,
  hasDataSourceConnection,
  NON_EVIDENCE_CATEGORIES,
} from "../connectorsCatalog"

describe("NON_EVIDENCE_CATEGORIES (not data sources)", () => {
  it("excludes pm, code, design, and docs", () => {
    expect([...NON_EVIDENCE_CATEGORIES].sort()).toEqual([
      "code",
      "design",
      "docs",
      "pm",
    ])
  })

  it("no longer names a category the catalog doesn't have", () => {
    // The deny-list is keyed on category keys, so a stale entry is silently
    // dead code — this pins it to reality.
    for (const key of NON_EVIDENCE_CATEGORIES) {
      expect(
        CONNECTOR_CATALOG.some((c) => c.key === key),
        `${key} is denied but no such category exists`,
      ).toBe(true)
    }
  })
})

describe("isEvidenceConnector — data sources", () => {
  it("counts analytics, customer voice/support/calls/feedback, crm, revenue, monitoring", () => {
    for (const id of [
      "mixpanel", // analytics
      "amplitude",
      "google_analytics",
      "zendesk", // customer support (category voice)
      "intercom", // customer support — category voice even though its TYPE is communication
      "gong", // customer calls
      "fireflies",
      "dovetail", // feedback
      "hubspot", // crm
      "salesforce",
      "stripe", // revenue
      "sentry", // monitoring
      "datadog",
      "slack", // dual-typed communication + customer-voice (2026-07-30)
    ]) {
      expect(isEvidenceConnector(id), `${id} should be a data source`).toBe(true)
    }
  })

  it("does NOT count Teams, PM tools, code, design, or docs", () => {
    for (const id of [
      "msteams", // left the catalog with Communications — unknown ⇒ not evidence
      "jira", // pm
      "asana",
      "clickup",
      "linear",
      "github", // code
      "gitlab",
      "figma", // design — newly excluded per the boss's rule
      "framer",
      "notion", // docs — excluded per the boss's rule, 2026-07-22
      "google_drive",
    ]) {
      expect(isEvidenceConnector(id), `${id} should NOT be a data source`).toBe(false)
    }
  })

  it("returns false for unknown ids", () => {
    expect(isEvidenceConnector("not_a_real_provider")).toBe(false)
  })
})

describe("hasDataSourceConnection — the onboarding brief gate", () => {
  const active = (provider: string) => ({ provider, status: "active" })

  it("is false when nothing is connected", () => {
    expect(hasDataSourceConnection([])).toBe(false)
  })

  it("is false when only non-data-sources are connected (Teams + Jira + GitHub + Figma)", () => {
    expect(
      hasDataSourceConnection([
        active("msteams"),
        active("jira"),
        active("github"),
        active("figma"),
      ]),
    ).toBe(false)
  })

  it("is true when only Slack is connected (dual-typed — its synced channels are evidence)", () => {
    expect(hasDataSourceConnection([active("slack")])).toBe(true)
  })

  it("is false when only docs tools are connected (Notion + Google Docs)", () => {
    expect(
      hasDataSourceConnection([active("notion"), active("google_drive")]),
    ).toBe(false)
  })

  it("is true when any real data source is active (e.g. Zendesk) alongside non-sources", () => {
    expect(
      hasDataSourceConnection([active("msteams"), active("zendesk")]),
    ).toBe(true)
  })

  it("ignores non-active connections (a revoked analytics connector does not count)", () => {
    expect(
      hasDataSourceConnection([{ provider: "mixpanel", status: "revoked" }]),
    ).toBe(false)
    expect(
      hasDataSourceConnection([{ provider: "mixpanel", status: "active" }]),
    ).toBe(true)
  })

  it("agrees with hasEvidenceConnector on the active provider ids", () => {
    expect(hasDataSourceConnection([active("hubspot")])).toBe(
      hasEvidenceConnector(["hubspot"]),
    )
  })
})
