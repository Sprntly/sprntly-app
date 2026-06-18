// @vitest-environment jsdom
//
// Unit tests for the /connectors/return tab logic: signal the original tab
// (BroadcastChannel + localStorage), close this tab, and fall back to a
// navigation when close is blocked.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  CONNECTOR_CHANNEL,
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
  it("posts a connector-connected message on the channel", async () => {
    const received: unknown[] = []
    const listener = new BroadcastChannel(CONNECTOR_CHANNEL)
    listener.onmessage = (ev) => received.push(ev.data)

    const ok = broadcastConnected("slack")
    expect(ok).toBe(true)
    // BroadcastChannel delivery is async in jsdom — flush microtasks/macrotasks.
    await new Promise((r) => setTimeout(r, 0))
    listener.close()

    expect(received).toContainEqual({
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
  it("broadcasts, writes storage, and closes the tab", async () => {
    const close = vi.spyOn(window, "close").mockImplementation(() => {})
    const received: unknown[] = []
    const listener = new BroadcastChannel(CONNECTOR_CHANNEL)
    listener.onmessage = (ev) => received.push(ev.data)

    handleConnectorReturn({ provider: "slack", returnTo: "/onboarding/connectors" })

    await new Promise((r) => setTimeout(r, 0))
    listener.close()

    expect(close).toHaveBeenCalled()
    expect(window.localStorage.getItem(CONNECTOR_STORAGE_KEY)).not.toBeNull()
    expect(received).toContainEqual({
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
