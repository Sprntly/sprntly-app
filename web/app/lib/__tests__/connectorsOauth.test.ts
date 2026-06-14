// @vitest-environment jsdom
//
// Unit tests for openOauthTab — the popup-blocker-safe helper that connects
// connectors in a NEW tab instead of navigating the current tab away.
import { afterEach, describe, expect, it, vi } from "vitest"
import { openOauthTab } from "../connectorsOauth"

const AUTH_URL = "https://slack.com/oauth/v2/authorize?client_id=abc"

afterEach(() => {
  vi.restoreAllMocks()
})

describe("openOauthTab", () => {
  it("opens a blank tab synchronously, then points it at the authorize URL", () => {
    const fakeTab = { closed: false, location: { href: "" }, close: vi.fn() }
    const open = vi
      .spyOn(window, "open")
      .mockReturnValue(fakeTab as unknown as Window)

    const pending = openOauthTab()
    // The tab is opened immediately (inside the user gesture), before any URL
    // is known — so the popup blocker treats it as gesture-initiated. Always a
    // NEW tab (_blank), never the current one.
    expect(open).toHaveBeenCalledWith("about:blank", "_blank")
    expect(fakeTab.location.href).toBe("")

    pending.finish(AUTH_URL)
    expect(fakeTab.location.href).toBe(AUTH_URL)
  })

  it("severs the opener link on the new tab (reverse-tabnabbing protection)", () => {
    const fakeTab: {
      closed: boolean
      location: { href: string }
      opener: unknown
      close: ReturnType<typeof vi.fn>
    } = { closed: false, location: { href: "" }, opener: window, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(fakeTab as unknown as Window)

    openOauthTab()
    // The security half of `noopener,noreferrer`: the provider tab must not be
    // able to reach back into the app tab via window.opener.
    expect(fakeTab.opener).toBeNull()
  })

  it("abort() closes the pre-opened tab", () => {
    const fakeTab = { closed: false, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(fakeTab as unknown as Window)

    const pending = openOauthTab()
    pending.abort()
    expect(fakeTab.close).toHaveBeenCalled()
  })

  it("falls back to a same-tab navigation when the popup was blocked", () => {
    // Popup blockers make window.open return null.
    vi.spyOn(window, "open").mockReturnValue(null)
    const hrefSetter = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { set href(v: string) { hrefSetter(v) } },
    })

    const pending = openOauthTab()
    pending.finish(AUTH_URL)
    expect(hrefSetter).toHaveBeenCalledWith(AUTH_URL)
    // abort is a no-op when there's no tab — must not throw.
    expect(() => pending.abort()).not.toThrow()
  })

  it("does not navigate a tab the user already closed", () => {
    const fakeTab = { closed: true, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(fakeTab as unknown as Window)
    const hrefSetter = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { set href(v: string) { hrefSetter(v) } },
    })

    const pending = openOauthTab()
    pending.finish(AUTH_URL)
    // Closed tab → fall back to current-tab navigation rather than writing to
    // a dead window.
    expect(fakeTab.location.href).toBe("")
    expect(hrefSetter).toHaveBeenCalledWith(AUTH_URL)
  })
})
