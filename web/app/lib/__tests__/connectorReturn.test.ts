// @vitest-environment jsdom
//
// Unit tests for the /connectors/return tab logic: signal the original tab
// (BroadcastChannel + localStorage), close this tab, and fall back to a
// navigation when close is blocked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  CONNECTOR_CONNECTED_MESSAGE,
  CONNECTOR_STORAGE_KEY,
  broadcastConnected,
  handleConnectorReturn,
  sanitizeReturnTo,
  writeStorageSignal,
} from "../connectorReturn"

afterEach(() => {
  vi.restoreAllMocks()
  try {
    window.localStorage.clear()
  } catch {
    /* ignore */
  }
})

describe("sanitizeReturnTo", () => {
  it("accepts relative single-slash paths", () => {
    expect(sanitizeReturnTo("/onboarding/connectors")).toBe("/onboarding/connectors")
    expect(sanitizeReturnTo("/settings?section=connectors")).toBe(
      "/settings?section=connectors",
    )
  })

  it("rejects absolute / protocol-relative / backslash / empty", () => {
    expect(sanitizeReturnTo(null)).toBeNull()
    expect(sanitizeReturnTo("")).toBeNull()
    expect(sanitizeReturnTo("//evil.com")).toBeNull()
    expect(sanitizeReturnTo("https://evil.com")).toBeNull()
    expect(sanitizeReturnTo("/\\evil.com")).toBeNull()
    expect(sanitizeReturnTo("onboarding/4")).toBeNull()
  })
})

describe("broadcastConnected", () => {
  it("posts a connector-connected message on the channel", () => {
    // Assert the post directly rather than relying on cross-channel async
    // delivery, which is non-deterministic in jsdom and flaked in CI.
    const postSpy = vi.spyOn(BroadcastChannel.prototype, "postMessage")

    const ok = broadcastConnected("slack")

    expect(ok).toBe(true)
    expect(postSpy).toHaveBeenCalledWith({
      type: CONNECTOR_CONNECTED_MESSAGE,
      provider: "slack",
    })
  })

  it("returns false (no throw) when BroadcastChannel is absent", () => {
    const g = globalThis as { BroadcastChannel?: unknown }
    const original = g.BroadcastChannel
    delete g.BroadcastChannel
    try {
      expect(broadcastConnected("github")).toBe(false)
    } finally {
      g.BroadcastChannel = original
    }
  })
})

describe("writeStorageSignal", () => {
  it("writes a provider + timestamp payload to localStorage", () => {
    expect(writeStorageSignal("figma")).toBe(true)
    const raw = window.localStorage.getItem(CONNECTOR_STORAGE_KEY)
    expect(raw).not.toBeNull()
    const parsed = JSON.parse(raw as string)
    expect(parsed.provider).toBe("figma")
    expect(typeof parsed.t).toBe("number")
  })
})

describe("handleConnectorReturn", () => {
  it("broadcasts, writes storage, and closes the tab", () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => {})
    // Assert the broadcast via the post spy rather than relying on real
    // cross-channel delivery, which is non-deterministic in jsdom and flakes
    // depending on test-file scheduling (same approach as broadcastConnected).
    const postSpy = vi.spyOn(BroadcastChannel.prototype, "postMessage")

    handleConnectorReturn({ provider: "slack", returnTo: "/onboarding/connectors" })

    expect(close).toHaveBeenCalled()
    expect(window.localStorage.getItem(CONNECTOR_STORAGE_KEY)).not.toBeNull()
    expect(postSpy).toHaveBeenCalledWith({
      type: CONNECTOR_CONNECTED_MESSAGE,
      provider: "slack",
    })
  })

  it("falls back to navigating to the sanitized return_to when close is blocked", () => {
    // window.close is a no-op (blocked); the scheduled fallback should redirect.
    vi.spyOn(window, "close").mockImplementation(() => {})
    const replace = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { replace, search: "" },
    })
    // Run the scheduled fallback synchronously.
    const schedule = (fn: () => void) => fn()

    handleConnectorReturn({
      provider: "slack",
      returnTo: "/settings?section=connectors",
      schedule,
    })

    expect(replace).toHaveBeenCalledWith(
      "/settings?section=connectors&connected=slack",
    )
  })

  it("defaults to /onboarding/connectors when return_to is unsafe/absent", () => {
    vi.spyOn(window, "close").mockImplementation(() => {})
    const replace = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { replace, search: "" },
    })
    const schedule = (fn: () => void) => fn()

    handleConnectorReturn({ provider: "github", returnTo: "https://evil.com", schedule })

    expect(replace).toHaveBeenCalledWith("/onboarding/connectors?connected=github")
  })

  it("does not redirect when the tab already closed", () => {
    vi.spyOn(window, "close").mockImplementation(() => {})
    const replace = vi.fn()
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { replace, search: "" },
    })
    Object.defineProperty(window, "closed", { configurable: true, value: true })
    const schedule = (fn: () => void) => fn()

    handleConnectorReturn({ provider: "slack", returnTo: "/x", schedule })

    expect(replace).not.toHaveBeenCalled()
    // Reset for other tests.
    Object.defineProperty(window, "closed", { configurable: true, value: false })
  })
})
