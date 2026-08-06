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

  it("links to the Fireflies API-key page so the user can copy their key", () => {
    const html = render({ item: FIREFLIES_ITEM, authType: "apikey" })
    expect(html).toContain(
      'href="https://app.fireflies.ai/integrations/custom/fireflies"',
    )
    expect(html).toContain("Fireflies API settings")
    // Opens the provider in a new tab, safely.
    expect(html).toMatch(/rel="noopener noreferrer"/)
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

const GOOGLE_MEET_ITEM: ConnectorItemRow = {
  id: "google_meet",
  name: "Google Meet",
  logo: "M",
  logoText: "M",
  logoColor: "#00832D",
}

describe("ConnectorConnectModalView — Google Meet pre-connect copy", () => {
  // Meet's coverage is genuinely NARROWER than the Zoom card sitting next to it
  // on the same shelf, and someone who has connected Zoom will reasonably
  // assume they match. A customer holding the wrong model reads a correct,
  // complete sync as a broken one — so these limits are asserted as shipped
  // copy, not left as documentation nobody reads.
  const html = () => render({ item: GOOGLE_MEET_ITEM }).toLowerCase()

  it("says coverage is only the connected account's own meetings", () => {
    expect(html()).toContain("only sees meetings the connected account organized")
    expect(html()).toContain("connects their own google account")
  })

  it("names the Workspace edition that can transcribe at all", () => {
    expect(html()).toContain("business standard or higher")
  })

  it("says transcripts must be on BEFORE the meeting, and links the help page", () => {
    const out = render({ item: GOOGLE_MEET_ITEM })
    expect(out.toLowerCase()).toContain("before a meeting starts")
    expect(out.toLowerCase()).toContain("won&#x27;t transcribe a call after the fact")
    expect(out).toContain("https://support.google.com/meet/answer/12849897")
    // New tab, so a half-finished connect flow is not lost behind it.
    expect(out).toContain('target="_blank"')
    expect(out).toContain('rel="noopener noreferrer"')
  })

  it("promises transcript text only, never the recording", () => {
    expect(html()).toContain("never the recording video or audio")
  })

  it("states the 30-day ceiling, so an empty first sync is not read as a bug", () => {
    expect(html()).toContain("only the last 30 days")
    expect(html()).toContain("no older history to import")
  })

  it("keeps Zoom's own prerequisites untouched", () => {
    const zoom = render({
      item: { id: "zoom", name: "Zoom", logo: "Z", logoText: "Z", logoColor: "#0B5CFF" },
    }).toLowerCase()
    expect(zoom).toContain("zoom account admin")
    // …and does not leak Meet's very different limits onto the Zoom card.
    expect(zoom).not.toContain("only the last 30 days")
  })
})
