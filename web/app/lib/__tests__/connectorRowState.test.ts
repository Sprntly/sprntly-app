import { describe, expect, it } from "vitest"
import type { ConnectionSummary } from "../api"
import type { ConnectorItemRow } from "../../types/content"
import { getConnectorRowState } from "../connectorRowState"

function item(overrides: Partial<ConnectorItemRow> = {}): ConnectorItemRow {
  return {
    id: "mixpanel",
    logo: "M",
    name: "Mixpanel",
    logoText: "M",
    logoColor: "#7856FF",
    oauth: false,
    ...overrides,
  }
}

function activeConnection(provider = "mixpanel"): ConnectionSummary {
  return {
    id: "conn1",
    provider,
    status: "active",
    google_email: null,
    account_label: "alice@meridian.health",
    scopes: "",
    config: {},
    last_sync_at: "2026-06-01T10:00:00Z",
    last_sync_error: null,
    created_at: "2026-05-30T00:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
  }
}

describe("getConnectorRowState", () => {
  it("Active when a matching active connection exists", () => {
    const s = getConnectorRowState(item({ id: "google_drive", oauth: true }), activeConnection("google_drive"))
    expect(s.status).toBe("active")
    expect(s.actionLabel).toBe("Configure")
    expect(s.canClick).toBe(true)
  })

  it("Off + Connect when no connection AND backend supports OAuth", () => {
    const s = getConnectorRowState(item({ id: "google_drive", oauth: true }), null)
    expect(s.status).toBe("off")
    expect(s.actionLabel).toBe("Connect")
    expect(s.canClick).toBe(true)
  })

  it("Off + 'Coming soon' (disabled) when no connection AND no OAuth backend", () => {
    const s = getConnectorRowState(item({ id: "mixpanel", oauth: false }), null)
    expect(s.status).toBe("off")
    expect(s.actionLabel).toBe("Coming soon")
    expect(s.canClick).toBe(false)
  })

  it("treats a non-active connection (status='error' etc) as Off", () => {
    const broken = { ...activeConnection("figma"), status: "error" }
    const s = getConnectorRowState(item({ id: "figma", oauth: true }), broken)
    expect(s.status).toBe("off")
    expect(s.actionLabel).toBe("Connect")
    expect(s.disconnected).toBe(false)
  })

  it("flags disconnected when an active connection's health probe failed", () => {
    const dead = { ...activeConnection("figma"), health: "disconnected" }
    const s = getConnectorRowState(item({ id: "figma", oauth: true }), dead)
    // Still configured (active + Configure) but flagged for reconnect.
    expect(s.status).toBe("active")
    expect(s.actionLabel).toBe("Configure")
    expect(s.disconnected).toBe(true)
    expect(s.statsString).toBe("Disconnected — reconnect")
  })

  it("does NOT flag disconnected when health is connected or unset", () => {
    const healthy = { ...activeConnection("figma"), health: "connected" }
    expect(
      getConnectorRowState(item({ id: "figma", oauth: true }), healthy).disconnected,
    ).toBe(false)
    // health absent (never checked) → not disconnected
    expect(
      getConnectorRowState(item({ id: "figma", oauth: true }), activeConnection("figma"))
        .disconnected,
    ).toBe(false)
  })
})

describe("getConnectorRowState — stats string", () => {
  it("shows account label when present and connected", () => {
    const s = getConnectorRowState(item({ id: "google_drive", oauth: true }), activeConnection("google_drive"))
    expect(s.statsString).toBe("alice@meridian.health")
  })

  it("shows 'Not connected' when there's no connection", () => {
    const s = getConnectorRowState(item({ oauth: true }), null)
    expect(s.statsString).toBe("Not connected")
  })

  it("falls back to 'Connected' when active but no account label", () => {
    const conn = { ...activeConnection("github"), account_label: null, google_email: null }
    const s = getConnectorRowState(item({ id: "github", oauth: true }), conn)
    expect(s.statsString).toBe("Connected")
  })

  it("prefers google_email when account_label is empty", () => {
    const conn = {
      ...activeConnection("google_drive"),
      account_label: null,
      google_email: "user@example.com",
    }
    const s = getConnectorRowState(item({ id: "google_drive", oauth: true }), conn)
    expect(s.statsString).toBe("user@example.com")
  })
})
