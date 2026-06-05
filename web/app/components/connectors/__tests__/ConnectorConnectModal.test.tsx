// View tests for the onboarding ConnectorConnectModal.
// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ConnectorConnectModalView } from "../ConnectorConnectModal"
import type { ConnectionSummary } from "../../../lib/api"
import type { ConnectorItemRow } from "../../../types/content"

function noop() {}

const FIGMA_ITEM: ConnectorItemRow = {
  id: "figma",
  name: "Figma",
  logo: "F",
  logoText: "F",
  logoColor: "#222",
}

const FIREFLIES_ITEM: ConnectorItemRow = {
  id: "fireflies",
  name: "Fireflies",
  logo: "F",
  logoText: "F",
  logoColor: "#FFAD33",
  authType: "apikey",
}

const SLACK_ITEM: ConnectorItemRow = {
  id: "slack",
  name: "Slack",
  logo: "S",
  logoText: "S",
  logoColor: "#4A154B",
}

const activeConn = (provider: string): ConnectionSummary => ({
  id: "conn-1",
  provider,
  status: "active",
  account_label: "alice@meridian.health",
  google_email: null,
  scopes: "",
  config: {},
  last_sync_at: null,
  last_sync_error: null,
  created_at: "2026-06-05T10:00:00Z",
  updated_at: "2026-06-05T10:00:00Z",
})

type Props = React.ComponentProps<typeof ConnectorConnectModalView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    open: true,
    item: FIGMA_ITEM,
    connection: null,
    authType: "oauth",
    apiKey: "",
    apiKeyError: null,
    isSubmittingApiKey: false,
    isConnecting: false,
    oauthError: null,
    showCompleteOrRestart: false,
    onClose: noop,
    onSkipForLater: noop,
    onConnect: noop,
    onApiKeyChange: noop,
    onSubmitApiKey: noop,
    onCompleteFlow: noop,
    onRestartFlow: noop,
  }
  return renderToStaticMarkup(
    React.createElement(ConnectorConnectModalView, { ...defaults, ...override }),
  )
}

describe("ConnectorConnectModalView — closed state", () => {
  it("renders nothing when open=false", () => {
    const html = render({ open: false })
    expect(html).toBe("")
  })

  it("renders nothing when item is null even if open=true", () => {
    const html = render({ open: true, item: null })
    expect(html).toBe("")
  })
})

describe("ConnectorConnectModalView — pre-connect OAuth mode", () => {
  it("shows the connector name and the 'Connect with X' CTA", () => {
    const html = render()
    expect(html).toContain("Figma")
    expect(html).toContain("Connect with Figma")
  })

  it("disables the Connect button while a startOauth request is in flight", () => {
    const html = render({ isConnecting: true })
    expect(html).toContain("Connecting…")
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Connecting…<\/button>/)
  })

  it("shows an inline error when oauthError is set", () => {
    const html = render({ oauthError: "Provider is not configured on the server" })
    expect(html).toContain("Provider is not configured on the server")
  })

  it("renders 'Skip & mark for later' button next to Connect", () => {
    const html = render()
    expect(html).toContain("Skip &amp; mark for later")
  })
})

describe("ConnectorConnectModalView — pre-connect API-key mode (Fireflies)", () => {
  it("shows an API key input instead of an OAuth Connect button", () => {
    const html = render({ item: FIREFLIES_ITEM, authType: "apikey" })
    expect(html).toContain("API key")
    expect(html).toMatch(/<input[^>]*type="(password|text)"/)
    // The OAuth-style "Connect with X" CTA should NOT be present.
    expect(html).not.toContain("Connect with Fireflies")
  })

  it("disables Submit until the api key is non-empty", () => {
    const html = render({
      item: FIREFLIES_ITEM,
      authType: "apikey",
      apiKey: "",
    })
    expect(html).toMatch(/<button[^>]*disabled[^>]*>(Connect|Save)/)
  })

  it("enables Submit when the api key is non-empty", () => {
    const html = render({
      item: FIREFLIES_ITEM,
      authType: "apikey",
      apiKey: "ff-some-key",
    })
    // Negative — no `disabled` attr on the Submit button
    expect(html).not.toMatch(/<button[^>]*disabled[^>]*>(Connect|Save)/)
  })

  it("shows 'Connecting…' while the api key is being submitted", () => {
    const html = render({
      item: FIREFLIES_ITEM,
      authType: "apikey",
      apiKey: "ff-some-key",
      isSubmittingApiKey: true,
    })
    expect(html).toContain("Connecting…")
  })

  it("surfaces an api-key error inline", () => {
    const html = render({
      item: FIREFLIES_ITEM,
      authType: "apikey",
      apiKey: "stale",
      apiKeyError: "Fireflies rejected this key.",
    })
    expect(html).toContain("Fireflies rejected this key.")
  })
})

describe("ConnectorConnectModalView — connected state", () => {
  it("shows the account label when the connector is active", () => {
    const html = render({
      item: FIGMA_ITEM,
      connection: activeConn("figma"),
    })
    expect(html).toContain("Connected as")
    expect(html).toContain("alice@meridian.health")
  })

  it("no longer shows the 'Connect with X' CTA when connected", () => {
    const html = render({
      item: FIGMA_ITEM,
      connection: activeConn("figma"),
    })
    expect(html).not.toContain("Connect with Figma")
  })

  it("shows a Done button to close the modal", () => {
    const html = render({
      item: FIGMA_ITEM,
      connection: activeConn("figma"),
    })
    expect(html).toContain("Done")
  })

  it("renders the children slot for provider-specific config (Slack picker, Drive picker, etc.)", () => {
    const html = renderToStaticMarkup(
      React.createElement(
        ConnectorConnectModalView,
        {
          open: true,
          item: SLACK_ITEM,
          connection: activeConn("slack"),
          authType: "oauth",
          apiKey: "",
          apiKeyError: null,
          isSubmittingApiKey: false,
          isConnecting: false,
          oauthError: null,
          showCompleteOrRestart: false,
          onClose: noop,
          onSkipForLater: noop,
          onConnect: noop,
          onApiKeyChange: noop,
          onSubmitApiKey: noop,
          onCompleteFlow: noop,
          onRestartFlow: noop,
        },
        React.createElement("div", { className: "test-slot" }, "(provider config)"),
      ),
    )
    expect(html).toContain("(provider config)")
    expect(html).toContain("test-slot")
  })
})

describe("ConnectorConnectModalView — in-flight prompt", () => {
  it("shows the complete-or-restart prompt when showCompleteOrRestart=true", () => {
    const html = render({ showCompleteOrRestart: true })
    expect(html.toLowerCase()).toContain("complete")
    // Restart option
    expect(html.toLowerCase()).toContain("start over")
  })

  it("prompt is suppressed when the connector is already connected", () => {
    const html = render({
      showCompleteOrRestart: true,
      connection: activeConn("figma"),
    })
    // Connected state wins — no mid-flow prompt confusion
    expect(html.toLowerCase()).not.toContain("start over")
  })
})
