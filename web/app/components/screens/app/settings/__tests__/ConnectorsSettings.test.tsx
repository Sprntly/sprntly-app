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

describe("ConnectorsSettingsView — flat list (no category grouping)", () => {
  it("does not render category section headers or sub-labels", () => {
    const html = render()
    // The only `set-block-h` is the uploaded-files header, which isn't shown
    // when files is empty — so with no files there are no section headers.
    expect(html).not.toContain("set-block-h")
    expect(html).not.toContain("powers On-Call Agent")
    expect(html).not.toContain("Project Management")
    expect(html).not.toContain("Customer Voice")
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

  it("uses inline brand-color background on the logo box for letter-only connectors", () => {
    const html = render()
    // Connectors without a bundled SVG → brand-color box + letter.
    expect(html).toContain("background:#7856FF") // Mixpanel
    expect(html).toContain("background:#FF6E6E") // Heap
  })
})

describe("ConnectorsSettingsView — single upload control", () => {
  it("renders exactly one upload control (uploads are company-wide)", () => {
    const html = render()
    const matches = html.match(/class="set-conn-upload"/g) ?? []
    expect(matches.length).toBe(1)
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

  it("shows the idle 'Upload files' label and an enabled input by default", () => {
    const html = render()
    expect(html).toContain("Upload files")
    expect(html).toContain("ti-cloud-upload")
    // Idle: no busy markers, input is selectable.
    expect(html).not.toContain("Uploading…")
    expect(html).not.toContain("is-uploading")
    expect(html).not.toMatch(/<input[^>]*disabled/)
  })

  it("shows an in-flight busy state (spinner + 'Uploading…') and disables the input while uploading", () => {
    const html = render({ uploading: true })
    expect(html).toContain("Uploading…")
    // Spinner icon swaps in; busy class + aria-busy drive the visible state.
    expect(html).toContain("ti-spin")
    expect(html).toContain("is-uploading")
    expect(html).toMatch(/aria-busy="true"/)
    // The file input is blocked so overlapping uploads can't be fired mid-flight.
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

  it("renders a flat list (no category headers) with one shared upload control", () => {
    const html = render({ categories: connectableCatalog() })
    expect(html).not.toContain("set-block-h")
    // 8 wired connector rows + one company-wide upload control.
    expect((html.match(/class="set-conn-row"/g) ?? []).length).toBe(8)
    expect((html.match(/class="set-conn-upload"/g) ?? []).length).toBe(1)
  })

  it("renders each connector's real brand logo from a locally bundled SVG", () => {
    const html = render({ categories: connectableCatalog() })
    // 6 of the 7 wired connectors have an official bundled SVG mark.
    for (const id of [
      "slack",
      "github",
      "figma",
      "hubspot",
      "clickup",
      "google_drive",
    ]) {
      expect(html).toContain(`src="/connectors/${id}.svg"`)
    }
    expect((html.match(/src="\/connectors\//g) ?? []).length).toBe(6)
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
