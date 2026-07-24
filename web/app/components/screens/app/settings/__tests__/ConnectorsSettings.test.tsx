// View tests for the Settings → Connectors pane (commit D; master/detail
// layout — category rail + one open category panel).
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
  resolveSelectedCategory,
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

/** Connector rows in the (single) open category panel. */
function countRows(html: string): number {
  return (html.match(/class="set-conn-row"/g) ?? []).length
}

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

describe("resolveSelectedCategory", () => {
  it("returns null when there are no categories", () => {
    expect(resolveSelectedCategory([], "analytics")).toBeNull()
  })

  it("defaults to the first category when nothing is selected", () => {
    expect(resolveSelectedCategory(CONNECTOR_CATALOG, null)?.key).toBe(
      CONNECTOR_CATALOG[0].key,
    )
  })

  it("returns the selected category when the key matches", () => {
    expect(resolveSelectedCategory(CONNECTOR_CATALOG, "design")?.key).toBe("design")
  })

  it("falls back to the first VISIBLE category for a stale/filtered-out key", () => {
    const visible = filterConnectorCategories(CONNECTOR_CATALOG, "jira")
    expect(resolveSelectedCategory(visible, "design")?.key).toBe("pm")
  })
})

describe("ConnectorsSettingsView — category rail (master column)", () => {
  it("renders one rail tab per catalog category, in catalog order", () => {
    const html = render()
    const keys = [...html.matchAll(/role="tab" id="conn-cat-tab-([a-z_]+)"/g)].map(
      (m) => m[1],
    )
    expect(keys).toEqual(CONNECTOR_CATALOG.map((c) => c.key))
    for (const cat of CONNECTOR_CATALOG) {
      // `&` in titles (e.g. "Voice of Customer & Support") is HTML-encoded.
      expect(html).toContain(cat.title.replace(/&/g, "&amp;"))
    }
    expect(html).toContain('role="tablist"')
  })

  it("selects the first category by default (dark pill + aria-selected)", () => {
    const html = render()
    const first = CONNECTOR_CATALOG[0]
    expect(html).toContain(
      `id="conn-cat-tab-${first.key}" aria-controls="conn-cat-panel-${first.key}" ` +
        'aria-selected="true" tabindex="0" class="set-conn-rail-item is-active"',
    )
    // Roving tabindex: every other tab is out of the page tab order.
    expect((html.match(/tabindex="0"/g) ?? []).length).toBe(1)
    expect((html.match(/tabindex="-1"/g) ?? []).length).toBe(
      CONNECTOR_CATALOG.length - 1,
    )
  })

  it("renders ONLY the selected category's panel and connector rows", () => {
    const html = render({ selectedCategoryKey: "design" })
    // One panel, and it's Design.
    expect((html.match(/role="tabpanel"/g) ?? []).length).toBe(1)
    expect(html).toContain('id="conn-cat-panel-design"')
    // Design's connectors are rendered; other categories' are not.
    expect(html).toContain("Figma")
    expect(html).toContain("Framer")
    expect(html).not.toContain("Slack")
    expect(html).not.toContain("Mixpanel")
    expect(countRows(html)).toBe(
      CONNECTOR_CATALOG.find((c) => c.key === "design")!.items.length,
    )
  })

  it("swaps the rendered connectors when another category is selected", () => {
    const design = render({ selectedCategoryKey: "design" })
    const comms = render({ selectedCategoryKey: "comms" })
    expect(design).toContain("Figma")
    expect(design).not.toContain("Slack")
    expect(comms).toContain("Slack")
    expect(comms).not.toContain("Figma")
    expect(comms).toContain('id="conn-cat-panel-comms"')
    expect(comms).toContain(
      'id="conn-cat-tab-comms" aria-controls="conn-cat-panel-comms" aria-selected="true"',
    )
  })

  it("shows the selected category's sub-label as the panel-head hint", () => {
    expect(render({ selectedCategoryKey: "analytics" })).toContain("· required")
    expect(render({ selectedCategoryKey: "monitoring" })).toContain(
      "· powers On-Call Agent",
    )
    // The hint belongs to the OPEN category only.
    expect(render({ selectedCategoryKey: "analytics" })).not.toContain(
      "· powers On-Call Agent",
    )
  })

  it("falls back to the first visible category when search hides the selection", () => {
    // "design" is selected but the query only leaves the PM category.
    const html = render({ selectedCategoryKey: "design", searchQuery: "jira" })
    expect(html).toContain('id="conn-cat-panel-pm"')
    expect(html).toContain("Jira")
    expect(html).not.toContain("Figma")
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
  it("renders only the OPEN category's rows, not all 41 catalog connectors", () => {
    const total = CONNECTOR_CATALOG.reduce((n, c) => n + c.items.length, 0)
    expect(total).toBe(41) // v6 catalog + Uploaded documents
    for (const cat of CONNECTOR_CATALOG) {
      expect(countRows(render({ selectedCategoryKey: cat.key }))).toBe(
        cat.items.length,
      )
    }
  })

  it("Asana row is wired for OAuth connect (no sync-engine support yet)", () => {
    const html = render({ selectedCategoryKey: "pm" })
    expect(html).toContain("Asana")
  })

  it("shows 'Off' pill + 'Connect' action for an apikey-supported connector with no connection", () => {
    const html = render({ selectedCategoryKey: "design" })
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
    const html = render({ selectedCategoryKey: "analytics" })
    // Mixpanel has oauth: false — should be disabled with "Coming soon".
    expect(html).toContain("Coming soon")
    // The Coming soon buttons should be disabled.
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Coming soon<\/button>/)
  })

  it("shows 'Active' pill + 'Configure' action when a matching active connection exists", () => {
    const map = new Map<string, ConnectionSummary>()
    map.set("figma", activeConn("figma", "design@meridian.health"))
    const html = render({ connectionByProvider: map, selectedCategoryKey: "design" })
    expect(html).toContain("Active")
    expect(html).toContain("Configure")
    expect(html).toContain("design@meridian.health")
  })

  it("uses inline brand-color background on the logo box for letter-only connectors", () => {
    const html = render({ selectedCategoryKey: "analytics" })
    // Connectors without a bundled SVG → brand-color box + letter.
    expect(html).toContain("background:#7856FF") // Mixpanel
    expect(html).toContain("background:#FF6E6E") // Heap
  })
})

describe("ConnectorsSettingsView — the open category's upload strip", () => {
  it("renders exactly one upload strip — the open category's", () => {
    const html = render({ selectedCategoryKey: "analytics" })
    const matches = html.match(/class="set-conn-upload"/g) ?? []
    expect(matches.length).toBe(1)
    expect(html).toContain("Upload files to this category")
  })

  it("hides the upload strip for integration-only categories (comms, code, pm)", () => {
    // Those categories must be populated by connecting the real integration,
    // so they opt out via `allowsManualUpload: false` in the catalog.
    for (const key of ["comms", "code", "pm"]) {
      const html = render({ selectedCategoryKey: key })
      expect(html).not.toContain("Upload files to this category")
      // The connector rows themselves are untouched.
      expect(countRows(html)).toBe(
        CONNECTOR_CATALOG.find((c) => c.key === key)!.items.length,
      )
    }
    expect(render({ selectedCategoryKey: "comms" })).toContain("Slack")
    expect(render({ selectedCategoryKey: "code" })).toContain("GitHub")
    expect(render({ selectedCategoryKey: "pm" })).toContain("Jira")
  })

  it("advertises the shared accepted-types hint", () => {
    const html = render({ selectedCategoryKey: "analytics" })
    // The `&` in the hint is HTML-encoded as `&amp;` by renderToStaticMarkup.
    expect(html).toContain(UPLOAD_ACCEPT_HINT.replace(/&/g, "&amp;"))
  })

  it("accepts the shared broad extension list", () => {
    const html = render({ selectedCategoryKey: "analytics" })
    expect(html).toContain(`accept="${UPLOAD_EXTENSIONS.join(",")}"`)
  })

  it("shows the idle upload labels and enabled inputs by default", () => {
    const html = render({ selectedCategoryKey: "analytics" })
    expect(html).toContain("ti-cloud-upload")
    // Idle: no busy markers, inputs are selectable.
    expect(html).not.toContain("Uploading…")
    expect(html).not.toContain("is-uploading")
    expect(html).not.toMatch(/<input[^>]*disabled/)
  })

  it("shows an in-flight busy state (spinner + 'Uploading…') and disables the inputs while uploading", () => {
    const html = render({ uploading: true, selectedCategoryKey: "analytics" })
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
  /** Every category's panel markup concatenated — the whole connectable set. */
  function renderAllPanels(): string {
    const cats = connectableCatalog()
    return cats
      .map((c) => render({ categories: cats, selectedCategoryKey: c.key }))
      .join("")
  }

  it("renders no 'Coming soon' rows when given connectableCatalog()", () => {
    const html = renderAllPanels()
    expect(html).not.toContain("Coming soon")
    expect(html).not.toMatch(/<button[^>]*disabled/)
  })

  it("shows the wired connectors and hides the 'Coming soon' ones", () => {
    const html = renderAllPanels()
    // Wired (kept):
    for (const name of ["Slack", "GitHub", "Figma", "ClickUp", "Jira", "Google Docs", "HubSpot", "Fireflies"]) {
      expect(html).toContain(name)
    }
    // Coming soon (removed):
    for (const name of ["Mixpanel", "Amplitude", "Sentry", "Linear", "Stripe", "MS Teams"]) {
      expect(html).not.toContain(name)
    }
  })

  it("puts the wired connectors in their categories (empty categories dropped)", () => {
    const keptCategories = connectableCatalog()
    // One rail tab per surviving category, and 12 wired connector rows spread
    // across them (one category's worth showing at a time).
    const one = render({ categories: keptCategories })
    expect((one.match(/role="tab" id="conn-cat-tab-/g) ?? []).length).toBe(
      keptCategories.length,
    )
    expect((one.match(/role="tabpanel"/g) ?? []).length).toBe(1)
    const rowsAcrossPanels = keptCategories.reduce(
      (n, c) =>
        n + countRows(render({ categories: keptCategories, selectedCategoryKey: c.key })),
      0,
    )
    expect(rowsAcrossPanels).toBe(12)
    // Each surviving category that allows manual upload shows its strip.
    expect(
      keptCategories.filter(
        (c) =>
          (render({ categories: keptCategories, selectedCategoryKey: c.key }).match(
            /class="set-conn-upload"/g,
          ) ?? []).length === 1,
      ).length,
    ).toBe(keptCategories.filter((c) => c.allowsManualUpload !== false).length)
    // Categories with no wired connectors (e.g. Monitoring) are dropped.
    expect(one).not.toContain("powers On-Call Agent")
  })

  it("renders each connector's real brand logo from a locally bundled SVG", () => {
    const html = renderAllPanels()
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

describe("ConnectorsSettingsView — uploaded document sources", () => {
  const source = {
    id: "s-1",
    name: "Q3 churn interviews",
    description: "12 enterprise accounts — why they left.",
    created_at: "2026-07-20T00:00:00Z",
    file_count: 3,
    files: [],
  }

  it("offers an add-a-source affordance on the uploads category", () => {
    const html = render()
    expect(html).toContain('data-testid="add-upload-source"')
    expect(html).toContain("Add a document source")
    expect(html).toContain("Name it, describe it, attach any files")
  })

  it("does not render the generic upload strip on the uploads category", () => {
    // Files are attached inside the named-source flow instead.
    expect(render()).not.toContain("Upload your documents export")
  })

  it("lists each source with its name, doc count and description", () => {
    const html = render({ uploadSources: [source] })
    expect(html).toContain('data-testid="upload-sources"')
    expect(html).toContain("Q3 churn interviews")
    expect(html).toContain("3 docs")
    expect(html).toContain("12 enterprise accounts")
  })

  it("singularizes a one-document source", () => {
    const html = render({ uploadSources: [{ ...source, file_count: 1 }] })
    expect(html).toContain("1 doc<")
  })

  it("offers add-files and remove per source", () => {
    const html = render({ uploadSources: [source] })
    expect(html).toContain("Add files")
    expect(html).toContain("Remove")
  })

  it("renders no source list when there are none", () => {
    expect(render()).not.toContain('data-testid="upload-sources"')
  })

  it("shows the uploads connector as Connect when off and Configure when active", () => {
    expect(render()).toContain("Uploaded documents")
    const active = render({
      connectionByProvider: new Map([["uploads", activeConn("uploads", "Your uploaded documents")]]),
    })
    expect(active).toContain("Your uploaded documents")
  })
})
