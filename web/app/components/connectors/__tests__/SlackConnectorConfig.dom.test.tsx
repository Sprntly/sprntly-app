// @vitest-environment jsdom
//
// The Slack connector's config slot, on BOTH surfaces that mount it: the
// Settings → Connectors "Configure" drawer and the post-OAuth connect modal.
//
// These two used to stack two unrelated questions on top of each other —
// where the Top Insights brief gets DELIVERED (SlackChannelPicker: "Direct
// message to me" / "A channel" / target dropdown / Save) above which channels
// the corpus sync PULLS from (SlackSyncChannelsPicker). Delivery is a
// per-user notification preference and has its own home in
// Settings → Comms & Brief, so as of 2026-08-04 the connector surfaces ask
// exactly one question: what do we read?
//
// The sibling View tests (ConfigureConnectorDrawer.test.tsx /
// ConnectorConnectModal.test.tsx) cover the pure Views, which take the slot as
// `children` — so the provider dispatch that chooses the slot was untested
// until this file. Mount the CONTAINERS to pin it.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const testConnectionMock = vi.fn()

// The ApiError stand-in is declared INSIDE the factory: vi.mock is hoisted
// above the module body, and unlike the lazily-called `testConnectionMock`
// arrow, a class referenced directly in the returned object is read the moment
// the factory runs — a top-level `class` would still be in its TDZ.
vi.mock("../../../lib/api", () => ({
  ApiError: class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown) {
      super(`api error ${status}`)
      this.status = status
      this.body = body
    }
  },
  apiErrorMessage: (_status: number, body: unknown) => String(body),
  connectorsApi: {
    testConnection: (...a: unknown[]) => testConnectionMock(...a),
  },
}))

// The real pickers fetch Slack channels on mount; stub both to markers so the
// assertions are about WHICH sections render, not what they fetch.
vi.mock("../SlackChannelPicker", () => ({
  SlackChannelPicker: () =>
    React.createElement("div", { "data-testid": "slack-delivery-picker" }),
}))
vi.mock("../SlackSyncChannelsPicker", () => ({
  SlackSyncChannelsPicker: () =>
    React.createElement("div", { "data-testid": "slack-sync-picker" }),
}))

import { ConfigureConnectorDrawer } from "../ConfigureConnectorDrawer"
import { ConnectorConnectModal } from "../ConnectorConnectModal"
import type { ConnectionSummary } from "../../../lib/api"

function slackConn(config: Record<string, unknown> = {}): ConnectionSummary {
  return {
    id: "c-slack",
    provider: "slack",
    status: "active",
    google_email: null,
    account_label: "meridian-health.slack.com",
    scopes: "",
    config,
    last_sync_at: "2026-08-01T10:00:00Z",
    last_sync_error: null,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-08-01T10:00:00Z",
  } as ConnectionSummary
}

function mountDrawer(connection: ConnectionSummary) {
  testConnectionMock.mockResolvedValue({ account_label: "meridian-health" })
  return render(
    React.createElement(ConfigureConnectorDrawer, {
      providerId: "slack",
      connection,
      activeCompany: "meridian-health",
      onClose: () => {},
      onDisconnected: () => {},
    }),
  )
}

function mountModal(connection: ConnectionSummary) {
  return render(
    React.createElement(ConnectorConnectModal, {
      providerId: "slack",
      activeCompany: "meridian-health",
      connection,
      returnTo: "/onboarding/connectors",
      onClose: () => {},
      onConnected: () => {},
      onSkipForLater: () => {},
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("Slack config slot — Settings → Connectors Configure drawer", () => {
  it("renders the sync-channel picker and NOT the brief-delivery picker", async () => {
    mountDrawer(slackConn({ sync_channel_ids: ["C1"] }))
    await waitFor(() => {
      expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    })
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
  })

  it("shows the same sync-only slot on the company's shared connection", async () => {
    // The old `company_connection` branch swapped the delivery picker for a
    // "connect Slack from Settings → Notifications" hint, because delivery was
    // per-user. Channel selection is COMPANY-wide, so with delivery gone there
    // is nothing left to branch on — a member sees exactly what an admin sees.
    mountDrawer(slackConn({ company_connection: true }))
    await waitFor(() => {
      expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    })
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
    expect(document.body.textContent).not.toContain(
      "Settings → Notifications",
    )
  })

  it("keeps the sync button beside the picker", async () => {
    mountDrawer(slackConn())
    await waitFor(() => {
      expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    })
    // Save-channels lives inside the stubbed picker; the drawer's own Slack
    // affordance is the sync trigger, and Disconnect is the View's.
    expect(screen.getByText("Sync Slack Data")).not.toBeNull()
    expect(screen.getByText("Disconnect")).not.toBeNull()
  })

  it("ignores a saved delivery target — it is Comms & Brief's state, not ours", async () => {
    // A workspace that configured delivery before this change still has
    // target_type/channel_id on the connection row. The drawer must not
    // resurrect a picker for it.
    mountDrawer(
      slackConn({
        target_type: "channel",
        channel_id: "C0PRODUCT",
        channel_name: "#product",
      }),
    )
    await waitFor(() => {
      expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    })
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
    expect(document.body.textContent).not.toContain("#product")
  })
})

describe("Slack config slot — post-OAuth connect modal", () => {
  it("renders the sync-channel picker and NOT the brief-delivery picker", () => {
    mountModal(slackConn({ sync_channel_ids: [] }))
    expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
  })

  it("shows the same sync-only slot on the company's shared connection", () => {
    mountModal(slackConn({ company_connection: true }))
    expect(screen.getByTestId("slack-sync-picker")).not.toBeNull()
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
  })

  it("renders no config slot at all before the connection exists", () => {
    render(
      React.createElement(ConnectorConnectModal, {
        providerId: "slack",
        activeCompany: "meridian-health",
        connection: null,
        returnTo: "/onboarding/connectors",
        onClose: () => {},
        onConnected: () => {},
        onSkipForLater: () => {},
      }),
    )
    expect(screen.queryByTestId("slack-sync-picker")).toBeNull()
    expect(screen.queryByTestId("slack-delivery-picker")).toBeNull()
  })
})
