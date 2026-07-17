// View tests for the Settings → Connectors pane (commit D).
// Same node-env SSR pattern as design-agent component tests
// (web/app/components/design-agent/__tests__/CompletionBar.test.tsx).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ADMIN_GATE_CONNECT_MESSAGE,
  ConnectorsSettingsView,
  apiKeyHelp,
  connectStartErrorMessage,
  filterConnectorCategories,
  isAdminGateError,
} from "../ConnectorsSettings"
import {
  CONNECTOR_CATALOG,
  connectableCatalog,
} from "../../../../../lib/connectorsCatalog"
import {
  UPLOAD_ACCEPT_HINT,
  UPLOAD_EXTENSIONS,
} from "../../../../../lib/sources-helpers"
import {
  ApiError,
  type ConnectionSummary,
  type SourceFile,
} from "../../../../../lib/api"

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
    onRegenerateBrief: noop,
    regenerating: false,
    regenerateError: null,
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

describe("ConnectorsSettingsView — Regenerate brief", () => {
  it("renders the Regenerate brief action and its explainer copy", () => {
    const html = render()
    expect(html).toContain("Regenerate brief")
    expect(html).toContain(
      "Digest new sources and rebuild your weekly brief",
    )
    // Idle button is enabled and labeled "Regenerate brief".
    expect(html).toMatch(
      /<button[^>]*class="btn btn-primary set-conn-regen-btn"[^>]*>Regenerate brief<\/button>/,
    )
  })

  it("shows the spinner + 'Regenerating…' label and disables the button while in flight", () => {
    const html = render({ regenerating: true })
    expect(html).toContain("Regenerating…")
    expect(html).toContain("spinner")
    // The button carries the disabled + aria-busy attributes when regenerating.
    expect(html).toMatch(/<button[^>]*class="btn btn-primary set-conn-regen-btn"[^>]*disabled/)
    expect(html).toMatch(/aria-busy="true"/)
  })

  it("is not disabled when idle", () => {
    const html = render({ regenerating: false })
    expect(html).not.toMatch(
      /<button[^>]*class="btn btn-primary set-conn-regen-btn"[^>]*disabled/,
    )
  })

  it("surfaces a regenerate error inline", () => {
    const html = render({ regenerateError: "API 503" })
    expect(html).toContain("Could not regenerate brief: API 503")
  })

  it("renders no regenerate error banner when there is none", () => {
    expect(render()).not.toContain("Could not regenerate brief")
  })
})

describe("ConnectorsSettingsView — grouped by category", () => {
  it("renders one card per catalog category with its title", () => {
    const html = render()
    for (const cat of CONNECTOR_CATALOG) {
      expect(html).toContain(`data-category="${cat.key}"`)
      // `&` in titles (e.g. "Customer Voice & Support") is HTML-encoded.
      expect(html).toContain(cat.title.replace(/&/g, "&amp;"))
    }
    expect((html.match(/class="set-block sp-conn-cat"/g) ?? []).length).toBe(
      CONNECTOR_CATALOG.length,
    )
  })

  it("shows category sub-labels as the card-head hint", () => {
    const html = render()
    expect(html).toContain("· required") // Analytics
    expect(html).toContain("· powers On-Call Agent") // Monitoring
  })
})

describe("filterConnectorCategories — search matching", () => {
  it("returns the catalog unchanged for an empty/whitespace query", () => {
    expect(filterConnectorCategories(CONNECTOR_CATALOG, "")).toBe(CONNECTOR_CATALOG)
    expect(filterConnectorCategories(CONNECTOR_CATALOG, "   ")).toBe(CONNECTOR_CATALOG)
  })

  it("a category-title match keeps the WHOLE category (all its connectors)", () => {
    const out = filterConnectorCategories(CONNECTOR_CATALOG, "project management")
    expect(out.length).toBe(1)
    expect(out[0].key).toBe("pm")
    // Every PM connector survives, not just name matches.
    expect(out[0].items.map((i) => i.id)).toEqual(["linear", "jira", "clickup", "asana"])
  })

  it("a connector-name match keeps just those rows inside their category", () => {
    const out = filterConnectorCategories(CONNECTOR_CATALOG, "jira")
    expect(out.length).toBe(1)
    expect(out[0].key).toBe("pm")
    expect(out[0].items.map((i) => i.id)).toEqual(["jira"])
  })

  it("matches connector TYPE labels too (e.g. 'task management' → the PM tools)", () => {
    const out = filterConnectorCategories(CONNECTOR_CATALOG, "task management")
    expect(out.some((c) => c.items.some((i) => i.id === "clickup"))).toBe(true)
  })

  it("is case-insensitive and drops categories with no matches", () => {
    const out = filterConnectorCategories(CONNECTOR_CATALOG, "JIRA")
    expect(out.length).toBe(1)
    expect(filterConnectorCategories(CONNECTOR_CATALOG, "zzz-nope")).toEqual([])
  })
})

describe("ConnectorsSettingsView — search box", () => {
  it("renders the search input", () => {
    const html = render()
    expect(html).toContain('aria-label="Search connectors"')
    expect(html).toContain("Search connectors or categories")
  })

  it("with a query, renders only matching groups/rows", () => {
    const html = render({ searchQuery: "jira" })
    expect(html).toContain("Jira")
    expect(html).not.toContain("Figma")
    expect((html.match(/class="set-conn-row"/g) ?? []).length).toBe(1)
  })

  it("shows a no-results message for a query matching nothing", () => {
    const html = render({ searchQuery: "zzz-nope" })
    expect(html).toContain('data-testid="conn-search-empty"')
    expect(html).toContain("No connectors or categories match")
    expect((html.match(/class="set-conn-row"/g) ?? []).length).toBe(0)
  })
})

describe("apiKeyHelp — api-key modal help copy", () => {
  it("links Fireflies straight to its API-key page so the user can copy the key", () => {
    const html = renderToStaticMarkup(<>{apiKeyHelp("fireflies", "Fireflies")}</>)
    expect(html).toContain(
      'href="https://app.fireflies.ai/integrations/custom/fireflies"',
    )
    expect(html).toContain("Fireflies API settings")
    expect(html).toMatch(/rel="noopener noreferrer"/)
  })

  it("returns null for a connector with no known key page", () => {
    expect(apiKeyHelp("unknown", "Unknown")).toBeNull()
  })
})

describe("ConnectorsSettingsView — per-row behavior", () => {
  it("renders 40 connector rows total (v6 catalog: + Segment, App/Play Store, CRM roster)", () => {
    const html = render()
    const matches = html.match(/class="set-conn-row"/g) ?? []
    expect(matches.length).toBe(40)
  })

  it("Asana row is wired for OAuth connect (no sync-engine support yet)", () => {
    const html = render()
    expect(html).toContain("Asana")
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

  it("uses inline brand-color background on the logo box for letter-only connectors", () => {
    const html = render()
    // Connectors without a bundled SVG → brand-color box + letter.
    expect(html).toContain("background:#7856FF") // Mixpanel
    expect(html).toContain("background:#FF6E6E") // Heap
  })
})

describe("ConnectorsSettingsView — per-category upload strips", () => {
  it("renders one upload strip per category card", () => {
    const html = render()
    const matches = html.match(/class="set-conn-upload"/g) ?? []
    expect(matches.length).toBe(CONNECTOR_CATALOG.length)
  })

  it("labels each strip with its category (e.g. 'Upload analytics export')", () => {
    const html = render()
    expect(html).toContain("Upload analytics export")
    expect(html).toContain("Upload project management export")
  })

  it("advertises the shared accepted-types hint", () => {
    const html = render()
    // The `&` in the hint is HTML-encoded as `&amp;` by renderToStaticMarkup.
    expect(html).toContain(UPLOAD_ACCEPT_HINT.replace(/&/g, "&amp;"))
  })

  it("accepts the shared broad extension list", () => {
    const html = render()
    expect(html).toContain(`accept="${UPLOAD_EXTENSIONS.join(",")}"`)
  })

  it("shows the idle upload labels and enabled inputs by default", () => {
    const html = render()
    expect(html).toContain("ti-cloud-upload")
    // Idle: no busy markers, inputs are selectable.
    expect(html).not.toContain("Uploading…")
    expect(html).not.toContain("is-uploading")
    expect(html).not.toMatch(/<input[^>]*disabled/)
  })

  it("shows an in-flight busy state (spinner + 'Uploading…') and disables the inputs while uploading", () => {
    const html = render({ uploading: true })
    expect(html).toContain("Uploading…")
    // Spinner icon swaps in; busy class + aria-busy drive the visible state.
    expect(html).toContain("ti-spin")
    expect(html).toContain("is-uploading")
    expect(html).toMatch(/aria-busy="true"/)
    // The file inputs are blocked so overlapping uploads can't be fired mid-flight.
    expect(html).toMatch(/<input[^>]*disabled/)
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
    for (const name of ["Slack", "GitHub", "Figma", "ClickUp", "Jira", "Google Docs", "HubSpot", "Fireflies"]) {
      expect(html).toContain(name)
    }
    // Coming soon (removed):
    for (const name of ["Mixpanel", "Amplitude", "Sentry", "Linear", "Stripe", "MS Teams"]) {
      expect(html).not.toContain(name)
    }
  })

  it("groups the wired connectors into their categories (empty categories dropped)", () => {
    const html = render({ categories: connectableCatalog() })
    const keptCategories = connectableCatalog()
    // 11 wired connector rows across the surviving categories, one upload
    // strip per surviving category.
    expect((html.match(/class="set-conn-row"/g) ?? []).length).toBe(11)
    expect((html.match(/class="set-block sp-conn-cat"/g) ?? []).length).toBe(
      keptCategories.length,
    )
    expect((html.match(/class="set-conn-upload"/g) ?? []).length).toBe(
      keptCategories.length,
    )
    // Categories with no wired connectors (e.g. Monitoring) are dropped.
    expect(html).not.toContain("powers On-Call Agent")
  })

  it("renders each connector's real brand logo from a locally bundled SVG", () => {
    const html = render({ categories: connectableCatalog() })
    // 8 of the 10 wired connectors have an official bundled SVG mark
    // (Fireflies and Sprinklr keep their letter glyphs).
    for (const id of [
      "slack",
      "github",
      "figma",
      "hubspot",
      "clickup",
      "jira",
      "google_drive",
      "asana",
    ]) {
      expect(html).toContain(`src="/connectors/${id}.svg"`)
    }
    expect((html.match(/src="\/connectors\//g) ?? []).length).toBe(8)
    // No runtime favicon fetch remains.
    expect(html).not.toContain("s2/favicons")
    // Fireflies has no bundled SVG, so it keeps its letter glyph (no <img>).
    expect(html).not.toContain("/connectors/fireflies.svg")
  })
})

describe("ConnectorsSettings — admin-gate connect error mapping", () => {
  it("detects a 403 ApiError as the admin gate", () => {
    const err = new ApiError(403, {
      detail:
        "Only admins can manage org-wide connectors. " +
        "Ask your workspace admin to connect this integration.",
    })
    expect(isAdminGateError(err)).toBe(true)
  })

  it("detects the admin-gate by message even without an ApiError status", () => {
    expect(
      isAdminGateError(
        new Error("Only admins can manage org-wide connectors."),
      ),
    ).toBe(true)
  })

  it("does NOT treat unrelated failures as the admin gate", () => {
    expect(isAdminGateError(new ApiError(500, "boom"))).toBe(false)
    expect(isAdminGateError(new Error("network down"))).toBe(false)
  })

  it("maps the admin gate to the friendly message (not the raw 'Could not start' string)", () => {
    const err = new ApiError(403, {
      detail: "Only admins can manage org-wide connectors.",
    })
    const msg = connectStartErrorMessage("google_drive", err)
    expect(msg).toBe(ADMIN_GATE_CONNECT_MESSAGE)
    expect(msg).not.toContain("Could not start")
    expect(msg).not.toContain("google_drive")
  })

  it("keeps the diagnostic message for non-admin-gate failures", () => {
    const msg = connectStartErrorMessage("figma", new Error("timeout"))
    expect(msg).toBe("Could not start figma connect: timeout")
  })

  it("renders the friendly admin message in the pane's error alert (DOM)", () => {
    const html = render({ loadError: ADMIN_GATE_CONNECT_MESSAGE })
    expect(html).toContain(
      "Only a workspace admin can connect org-wide sources like Google Drive",
    )
    expect(html).toContain("Ask an admin to set this up")
    // The raw diagnostic string must NOT leak through.
    expect(html).not.toContain("Could not start google_drive connect")
    // It surfaces in the alert region for accessibility.
    expect(html).toContain('role="alert"')
  })
})
