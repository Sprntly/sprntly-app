// View tests for the per-connector "Configure" drawer (commit E).
// Same node-env SSR pattern as the design-agent component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConfigureConnectorDrawerView } from "../ConfigureConnectorDrawer"
import type { ConnectionSummary } from "../../../lib/api"
import type { ConnectorItemRow } from "../../../types/content"

function noop() {}

const FIGMA_ITEM: ConnectorItemRow = {
  id: "figma",
  logo: "F",
  name: "Figma",
  logoText: "F",
  logoColor: "#F24E1E",
  oauth: true,
}

function activeConn(provider: string, label = "design@meridian.health"): ConnectionSummary {
  return {
    id: `c-${provider}`,
    provider,
    status: "active",
    google_email: null,
    account_label: label,
    scopes: "",
    config: {},
    last_sync_at: "2026-06-01T10:00:00Z",
    last_sync_error: null,
    created_at: "2026-05-15T00:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
  }
}

function render(
  override: Partial<React.ComponentProps<typeof ConfigureConnectorDrawerView>> = {},
): string {
  const defaults: React.ComponentProps<typeof ConfigureConnectorDrawerView> = {
    open: true,
    item: FIGMA_ITEM,
    connection: activeConn("figma"),
    onClose: noop,
    onDisconnect: noop,
    isDisconnecting: false,
    status: { kind: "connected", accountLabel: "design@meridian.health" },
  }
  return renderToStaticMarkup(
    React.createElement(ConfigureConnectorDrawerView, { ...defaults, ...override }),
  )
}

describe("ConfigureConnectorDrawerView", () => {
  it("renders nothing when item is null (no connector selected)", () => {
    const html = render({ item: null })
    expect(html).toBe("")
  })

  it("applies the 'open' class when open=true", () => {
    const html = render({ open: true })
    expect(html).toMatch(/class="drawer-overlay open"/)
    expect(html).toMatch(/class="drawer open"/)
  })

  it("omits the 'open' class when open=false (slides off-screen)", () => {
    const html = render({ open: false })
    expect(html).toMatch(/class="drawer-overlay"/)
    expect(html).not.toMatch(/class="drawer-overlay open"/)
    expect(html).toMatch(/class="drawer"/)
    expect(html).not.toMatch(/class="drawer open"/)
  })

  it("renders connector name as the drawer title", () => {
    expect(render()).toContain("Figma")
  })

  it("renders account label from the connection", () => {
    expect(render()).toContain("design@meridian.health")
  })

  it("renders the connected-since timestamp from the connection's created_at", () => {
    // Don't pin to a specific timezone — just confirm something rendered.
    const html = render()
    expect(html).toMatch(/Connected/)
    expect(html).toContain("2026")
  })

  it("renders a Disconnect button", () => {
    expect(render()).toContain("Disconnect")
  })

  it("shows 'Disconnecting…' on the button when isDisconnecting=true", () => {
    const html = render({ isDisconnecting: true })
    expect(html).toContain("Disconnecting…")
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Disconnecting…<\/button>/)
  })

  it("renders the children slot when provided (connector-specific config)", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        ConfigureConnectorDrawerView,
        {
          open: true,
          item: FIGMA_ITEM,
          connection: activeConn("figma"),
          onClose: noop,
          onDisconnect: noop,
          isDisconnecting: false,
          status: { kind: "connected" },
        },
        React.createElement("div", { "data-testid": "slot-content" }, "drive file picker"),
      ),
    )
    expect(html).toContain('data-testid="slot-content"')
    expect(html).toContain("drive file picker")
  })

  // ───── Auto connection-status badge (replaces the manual "Test connection") ─────

  it("no longer renders the manual 'Test connection' / 'Test now' control", () => {
    const html = render()
    expect(html).not.toContain("Test connection")
    expect(html).not.toContain("Test now")
  })

  it("shows a 'Connected' status badge with the account detail when connected", () => {
    const html = render({
      status: { kind: "connected", accountLabel: "alice@meridian.health" },
    })
    expect(html).toContain("Connected")
    expect(html).toContain("alice@meridian.health")
    expect(html).toMatch(/conn-config-status--connected/)
  })

  it("shows a 'Disconnected' status badge with the failure message", () => {
    const html = render({
      status: { kind: "disconnected", message: "Token rejected by provider" },
    })
    expect(html).toContain("Disconnected")
    expect(html).toContain("Token rejected by provider")
    expect(html).toMatch(/conn-config-status--disconnected/)
  })

  it("shows 'Checking…' while the probe is in flight", () => {
    const html = render({ status: { kind: "checking" } })
    expect(html).toContain("Checking…")
    expect(html).toMatch(/conn-config-status--checking/)
  })

  it("defaults to 'Checking…' when no status has resolved yet (status=null)", () => {
    const html = render({ status: null })
    expect(html).toContain("Checking…")
    expect(html).toMatch(/conn-config-status--checking/)
  })

  it("still renders the connector when connection is null but item is non-null", () => {
    // Edge case: drawer opened mid-flight; connection load not done yet.
    const html = render({ connection: null, status: { kind: "disconnected", message: "Not connected" } })
    expect(html).toContain("Figma")
    expect(html).toContain("Disconnected")
  })
})
