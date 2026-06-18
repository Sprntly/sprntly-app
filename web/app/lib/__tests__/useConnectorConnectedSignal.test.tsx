// @vitest-environment jsdom
//
// Tests for useConnectorConnectedSignal — the original Sprntly tab's listener
// that fires onConnected when the OAuth return tab signals (via
// BroadcastChannel or the localStorage storage-event fallback), and cleans up
// on unmount.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import {
  CONNECTOR_CHANNEL,
  CONNECTOR_CONNECTED_MESSAGE,
  CONNECTOR_STORAGE_KEY,
} from "../connectorReturn"
import { useConnectorConnectedSignal } from "../useConnectorConnectedSignal"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Harness({ onConnected }: { onConnected: (p: string) => void }) {
  useConnectorConnectedSignal(onConnected)
  return null
}

describe("useConnectorConnectedSignal", () => {
  it("invokes the callback on a BroadcastChannel connector-connected message", async () => {
    const onConnected = vi.fn()
    render(React.createElement(Harness, { onConnected }))

    const sender = new BroadcastChannel(CONNECTOR_CHANNEL)
    sender.postMessage({ type: CONNECTOR_CONNECTED_MESSAGE, provider: "slack" })
    await new Promise((r) => setTimeout(r, 0))
    sender.close()

    expect(onConnected).toHaveBeenCalledWith("slack")
  })

  it("invokes the callback on the storage-event fallback", () => {
    const onConnected = vi.fn()
    render(React.createElement(Harness, { onConnected }))

    // Simulate the storage event another tab's localStorage write triggers.
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: CONNECTOR_STORAGE_KEY,
        newValue: JSON.stringify({ provider: "github", t: Date.now() }),
      }),
    )

    expect(onConnected).toHaveBeenCalledWith("github")
  })

  it("ignores unrelated storage keys", () => {
    const onConnected = vi.fn()
    render(React.createElement(Harness, { onConnected }))

    window.dispatchEvent(
      new StorageEvent("storage", { key: "other_key", newValue: "x" }),
    )

    expect(onConnected).not.toHaveBeenCalled()
  })

  it("unsubscribes on unmount", async () => {
    const onConnected = vi.fn()
    const { unmount } = render(React.createElement(Harness, { onConnected }))
    unmount()

    // After unmount, neither signal should reach the callback.
    const sender = new BroadcastChannel(CONNECTOR_CHANNEL)
    sender.postMessage({ type: CONNECTOR_CONNECTED_MESSAGE, provider: "slack" })
    await new Promise((r) => setTimeout(r, 0))
    sender.close()
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: CONNECTOR_STORAGE_KEY,
        newValue: JSON.stringify({ provider: "github", t: Date.now() }),
      }),
    )

    expect(onConnected).not.toHaveBeenCalled()
  })
})
