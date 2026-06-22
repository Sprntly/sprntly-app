// View tests for the Settings → Connectors pane (commit D).
// Same node-env SSR pattern as design-agent component tests
// (web/app/components/design-agent/__tests__/CompletionBar.test.tsx).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConnectorsSettingsView } from "../ConnectorsSettings"
import {
  CONNECTOR_CATALOG,
  connectableCatalog,
} from "../../../../../lib/connectorsCatalog"
import {
  UPLOAD_ACCEPT_HINT,
  UPLOAD_EXTENSIONS,
} from "../../../../../lib/sources-helpers"
import type { ConnectionSummary, SourceFile } from "../../../../../lib/api"

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
    files: [],
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

  it("shows 'Off' pill + 'Connect' action for an apikey-supported connector with no connection", () => {
    const html = render()
    // Both `oauth: true` and `authType: "apikey"` rows surface a Connect action.
    expect(html).toContain("Figma")
    expect(html).toContain("Connect")
  })

  it("Figma row is routed to OAuth (PAT path fully removed for Figma app-review)", () => {
    // Figma's review rejected the PAT-based connect mechanism. The PAT path is
    // now removed entirely (no figma_pat module, no /figma/pat route); Figma is
    // OAuth-only — oauth:true and no authType.
    const figma = CONNECTOR_CATALOG.flatMap((c) => c.items).find(
      (i) => i.id === "figma",
    )
    expect(figma).toBeTruthy()
    expect(figma!.oauth).toBe(true)
    expect(figma!.authType).toBeUndefined()
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

  it("shows the broad accepted-types hint on every category", () => {
    const html = render()
    // FIX #3: all categories now advertise the same broad shared hint. The `&`
    // in the hint is HTML-encoded as `&amp;` by renderToStaticMarkup.
    const encoded = UPLOAD_ACCEPT_HINT.replace(/&/g, "&amp;")
    const matches = html.split(encoded).length - 1
    expect(matches).toBe(8)
  })

  it("attaches a broad accept= attribute (same on every category)", () => {
    const html = render()
    // FIX #3: every category accepts the shared broad extension list.
    const accept = UPLOAD_EXTENSIONS.join(",")
    const matches = html.match(
      new RegExp(`accept="${accept.replace(/\./g, "\\.")}"`, "g"),
    ) ?? []
    expect(matches.length).toBe(8)
  })
})

describe("ConnectorsSettingsView — uploaded files list (FIX #1)", () => {
  function sourceFile(filename: string, kind = "pdf"): SourceFile {
    return {
      filename,
      kind,
      size_bytes: 2048,
      md_chars: 100,
      added_at: "2026-06-01T10:00:00Z",
    }
  }

  it("renders nothing when there are no uploaded files", () => {
    expect(render({ files: [] })).not.toContain("Uploaded files")
  })

  it("renders a single company-wide list when files exist", () => {
    const html = render({
      files: [sourceFile("q2-metrics.pdf"), sourceFile("notes.txt", "txt")],
    })
    expect(html).toContain("Uploaded files")
    expect(html).toContain("across all categories")
    expect(html).toContain("q2-metrics.pdf")
    expect(html).toContain("notes.txt")
    // One shared list, not one per category.
    const lists = html.match(/class="src-list"/g) ?? []
    expect(lists.length).toBe(1)
  })
})

describe("ConnectorsSettingsView — Settings tab uses the connectable-only catalog", () => {
  it("renders no 'Coming soon' rows when given connectableCatalog()", () => {
    const html = render({ categories: connectableCatalog() })
    expect(html).not.toContain("Coming soon")
    expect(html).not.toMatch(/<button[^>]*disabled/)
  })

  it("shows the wired connectors and hides the 'Coming soon' ones", () => {
    const html = render({ categories: connectableCatalog() })
    // Wired (kept):
    for (const name of ["Slack", "GitHub", "Figma", "ClickUp", "Google Docs", "HubSpot", "Fireflies"]) {
      expect(html).toContain(name)
    }
    // Coming soon (removed):
    for (const name of ["Mixpanel", "Amplitude", "Sentry", "Linear", "Stripe", "MS Teams"]) {
      expect(html).not.toContain(name)
    }
  })

  it("drops empty categories (Analytics, Monitoring) and keeps upload strips for the 6 that remain", () => {
    const html = render({ categories: connectableCatalog() })
    expect(html).not.toContain("Analytics")
    expect(html).not.toContain("Monitoring")
    // 6 categories remain (PM, Voice, Revenue, Code, Design, Comms), each with
    // its own upload strip.
    expect((html.match(/set-conn-upload/g) ?? []).length).toBe(6)
  })
})
