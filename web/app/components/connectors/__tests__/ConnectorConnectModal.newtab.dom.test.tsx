// @vitest-environment jsdom
//
// Interaction tests for the shared ConnectorConnectModal's OAuth connect path.
// These assert the NEW-TAB behaviour: triggering "Connect with X" opens the
// provider's authorize URL in a NEW browser tab (window.open(..., "_blank"))
// and NEVER navigates the current tab (window.location.href is left alone), so
// the user keeps their place in onboarding / settings. The SSR-string view
// tests in ConnectorConnectModal.test.tsx can't reach handleConnect (it lives
// in the hooks-wired container), so this file drives a real click in jsdom.
//
// Matchers: native DOM only — NO @testing-library/jest-dom (repo convention).
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest"

// Sprntly components carry no `import React`; expose it globally (repo test
// convention — esbuild's classic JSX runtime).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Mock the canonical api module. The modal imports `connectorsApi` from
// `../../lib/api`; from this test file that resolves to `../../../lib/api`.
// Only `startOauth` is exercised on the OAuth connect path.
const { startOauthMock } = vi.hoisted(() => ({ startOauthMock: vi.fn() }))
vi.mock("../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/api")>()
  return {
    ...actual,
    connectorsApi: { ...actual.connectorsApi, startOauth: startOauthMock },
  }
})

import { ConnectorConnectModal } from "../ConnectorConnectModal"

// A fake provider tab so we can observe where the new tab gets pointed without
// letting jsdom actually navigate.
type FakeTab = { closed: boolean; location: { href: string }; opener: unknown; close: ReturnType<typeof vi.fn> }
let fakeTab: FakeTab
let openSpy: MockInstance<typeof window.open>
let hrefSetter: ReturnType<typeof vi.fn>

beforeEach(() => {
  fakeTab = { closed: false, location: { href: "" }, opener: window, close: vi.fn() }
  openSpy = vi.spyOn(window, "open").mockReturnValue(fakeTab as unknown as Window)

  // Intercept current-tab navigation so we can assert it NEVER happens on the
  // happy path (popup not blocked). jsdom would otherwise log a "not
  // implemented: navigation" error.
  hrefSetter = vi.fn()
  Object.defineProperty(window, "location", {
    configurable: true,
    value: new Proxy(
      {},
      {
        get: (_t, prop) => (prop === "href" ? "http://localhost/" : ""),
        set: (_t, prop, value) => {
          if (prop === "href") hrefSetter(value)
          return true
        },
      },
    ),
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

const baseProps = {
  activeCompany: "meridian-health",
  connection: null,
  returnTo: "/onboarding/connectors",
  onClose: () => {},
  onConnected: () => {},
  onSkipForLater: () => {},
}

// The main connectable OAuth providers, each routed through the shared modal.
// (Names mirror the catalog display names — note Google Drive shows as "Google
// Docs". Slack is excluded: it's authType:"apikey", not an OAuth-button flow.)
const OAUTH_PROVIDERS: Array<{ id: string; name: string; authorizeUrl: string }> = [
  { id: "figma", name: "Figma", authorizeUrl: "https://www.figma.com/oauth?state=abc" },
  { id: "github", name: "GitHub", authorizeUrl: "https://github.com/login/oauth/authorize?state=abc" },
  { id: "google_drive", name: "Google Docs", authorizeUrl: "https://accounts.google.com/o/oauth2/auth?state=abc" },
  { id: "clickup", name: "ClickUp", authorizeUrl: "https://app.clickup.com/api?state=abc" },
]

describe("ConnectorConnectModal — OAuth connect opens a NEW tab", () => {
  for (const provider of OAUTH_PROVIDERS) {
    it(`${provider.name}: click Connect opens _blank with the authorize URL and does NOT navigate the current tab`, async () => {
      startOauthMock.mockResolvedValue({ authorize_url: provider.authorizeUrl })
      const user = userEvent.setup()

      render(
        React.createElement(ConnectorConnectModal, {
          ...baseProps,
          providerId: provider.id,
        }),
      )

      await user.click(
        screen.getByRole("button", { name: new RegExp(`Connect with ${provider.name}`, "i") }),
      )

      // 1. A NEW tab was opened (synchronously, inside the gesture) as _blank.
      expect(openSpy).toHaveBeenCalledWith("about:blank", "_blank")
      // 2. startOauth was asked for THIS provider's authorize URL.
      await waitFor(() => expect(startOauthMock).toHaveBeenCalled())
      expect(startOauthMock.mock.calls[0][0]).toBe(provider.id)
      // 3. The new tab — not the current one — is pointed at the authorize URL.
      await waitFor(() => expect(fakeTab.location.href).toBe(provider.authorizeUrl))
      // 4. The current app tab is NEVER navigated away (the whole point).
      expect(hrefSetter).not.toHaveBeenCalled()
    })
  }

  it("Google Drive passes the active company as the start-oauth dataset", async () => {
    startOauthMock.mockResolvedValue({ authorize_url: "https://accounts.google.com/o/oauth2/auth" })
    const user = userEvent.setup()

    render(
      React.createElement(ConnectorConnectModal, {
        ...baseProps,
        providerId: "google_drive",
      }),
    )

    await user.click(screen.getByRole("button", { name: /Connect with Google Docs/i }))

    await waitFor(() => expect(startOauthMock).toHaveBeenCalled())
    // dataset arg (2nd) is the active company for Drive's folder-scoping.
    expect(startOauthMock.mock.calls[0][1]).toBe("meridian-health")
  })

  it("aborts the pre-opened tab (and does not navigate) when startOauth yields no URL", async () => {
    startOauthMock.mockResolvedValue({ authorize_url: "" })
    const user = userEvent.setup()

    render(
      React.createElement(ConnectorConnectModal, {
        ...baseProps,
        providerId: "figma",
      }),
    )

    await user.click(screen.getByRole("button", { name: /Connect with Figma/i }))

    await waitFor(() => expect(startOauthMock).toHaveBeenCalled())
    // No authorize URL → close the blank tab, never strand the user, and never
    // navigate the current tab.
    await waitFor(() => expect(fakeTab.close).toHaveBeenCalled())
    expect(fakeTab.location.href).toBe("")
    expect(hrefSetter).not.toHaveBeenCalled()
  })
})
