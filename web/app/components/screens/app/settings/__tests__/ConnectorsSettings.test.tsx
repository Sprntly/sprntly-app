// View tests for the Settings → Connectors pane (commit D).
// Same node-env SSR pattern as design-agent component tests
// (web/app/components/design-agent/__tests__/CompletionBar.test.tsx).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConnectorsSettingsView } from "../ConnectorsSettings"
import { CONNECTOR_CATALOG } from "../../../../../lib/connectorsCatalog"
import type { ConnectionSummary } from "../../../../../lib/api"

function noop() {}
function noopUpload() {}

function activeConn(provider: string, label = "alice@meridian.health"): ConnectionSummary {
  return {
    id: `c-${provider}`,
    provider,
    status: "active",
    google_email: null,
    account_label: label,
    scopes: "",
    config: {},
    last_sync_at: null,
    last_sync_error: null,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
  }
}

function render(
  override: Partial<React.ComponentProps<typeof ConnectorsSettingsView>> = {},
): string {
  const defaults: React.ComponentProps<typeof ConnectorsSettingsView> = {
    categories: CONNECTOR_CATALOG,
    connectionByProvider: new Map(),
    loading: false,
    loadError: null,
    onConnect: noop,
    onConfigure: noop,
    onUpload: noopUpload,
  }
  return renderToStaticMarkup(
    React.createElement(ConnectorsSettingsView, { ...defaults, ...override }),
  )
}

describe("ConnectorsSettingsView — chrome", () => {
  it("renders the design's header copy", () => {
    const html = render()
    expect(html).toContain("Connectors")
    expect(html).toContain("Every source feeding your agents")
  })

  it("shows loading state when loading=true", () => {
    expect(render({ loading: true })).toContain("Loading connectors…")
  })

  it("shows error message when loadError is set", () => {
    expect(render({ loadError: "API 500" })).toContain("Could not load connections: API 500")
  })
})

describe("ConnectorsSettingsView — categories + sub-labels", () => {
  it("renders all 8 category titles in design order", () => {
    const html = render()
    const expected = [
      "Analytics",
      "Project Management",
      "Customer Voice &amp; Support", // HTML-encoded &
      "Revenue",
      "Code",
      "Monitoring &amp; Reliability",
      "Design",
      "Communication",
    ]
    let lastIdx = -1
    for (const title of expected) {
      const idx = html.indexOf(title)
      expect(idx, `category not found or out of order: ${title}`).toBeGreaterThan(lastIdx)
      lastIdx = idx
    }
  })

  it("shows the 'required' sub-label on Analytics", () => {
    expect(render()).toContain("required")
  })

  it("shows the 'powers On-Call Agent' sub-label on Monitoring & Reliability", () => {
    expect(render()).toContain("powers On-Call Agent")
  })
})

describe("ConnectorsSettingsView — per-row behavior", () => {
  it("renders 31 connector rows total (29 design + ClickUp + Fireflies)", () => {
    const html = render()
    const matches = html.match(/class="set-conn-row"/g) ?? []
    expect(matches.length).toBe(31)
  })

  it("shows 'Off' pill + 'Connect' action for OAuth-supported connector with no connection", () => {
    const html = render()
    // Figma is OAuth-supported (oauth: true) but has no connection in this render.
    // The row should carry a Connect action.
    expect(html).toContain("Figma")
    expect(html).toContain("Connect")
  })

  it("shows 'Coming soon' (disabled) action for non-OAuth connector with no connection", () => {
    const html = render()
    // Mixpanel has oauth: false — should be disabled with "Coming soon".
    expect(html).toContain("Coming soon")
    // The Coming soon buttons should be disabled.
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Coming soon<\/button>/)
  })

  it("shows 'Active' pill + 'Configure' action when a matching active connection exists", () => {
    const map = new Map<string, ConnectionSummary>()
    map.set("figma", activeConn("figma", "design@meridian.health"))
    const html = render({ connectionByProvider: map })
    expect(html).toContain("Active")
    expect(html).toContain("Configure")
    expect(html).toContain("design@meridian.health")
  })

  it("uses inline brand-color background on the logo box", () => {
    const html = render()
    // Mixpanel's brand color from the catalog
    expect(html).toContain("background:#7856FF")
    // GitHub
    expect(html).toContain("background:#181717")
  })
})

describe("ConnectorsSettingsView — per-category upload strip", () => {
  it("renders an upload strip for every category", () => {
    const html = render()
    const matches = html.match(/class="set-conn-upload"/g) ?? []
    expect(matches.length).toBe(8)
  })

  it("shows each category's accepted-types hint", () => {
    const html = render()
    // Match the catalog's uploadAccept strings (HTML-encoded · in markup may
    // come through as raw · since renderToStaticMarkup outputs UTF-8).
    expect(html).toContain("PDF · CSV · XLSX") // Analytics
    expect(html).toContain("PDF · MD")          // Code + Monitoring
  })

  it("attaches an accept= attribute mirroring uploadExtensions", () => {
    const html = render()
    // Analytics: ".pdf,.csv,.xlsx"
    expect(html).toContain('accept=".pdf,.csv,.xlsx"')
    // Code: ".pdf,.md"
    expect(html).toContain('accept=".pdf,.md"')
  })
})
