// P6-05 (#8) — DesignAgentNotificationReplay tests. Node-env vitest (no jsdom,
// no @testing-library, effects do not fire under renderToStaticMarkup), so —
// following the repo convention — we SSR-render the component for the renders-
// null assertion and exercise the pure exported units the component composes
// (`replayCompletedNotifications` show/no-ack + `shouldAckOnClear` ack precision)
// directly. The component itself is a thin effect wrapper over those units.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// useNavigation throws without a NavigationProvider (and the provider pulls in
// next/navigation, unavailable in node-env). Mock it so the component renders.
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: () => {}, toast: null }),
}))

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (PrdSections/CompletionBar convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { DesignAgentNotificationReplay } from "../DesignAgentNotificationReplay"
import { replayCompletedNotifications } from "../DesignAgentDrawer"
import {
  __resetPageLoadGuards,
  acknowledge,
  getLastReplayShow,
  markCompleted,
  pendingCompleted,
  shouldAckOnClear,
} from "../notificationStore"

const READY = "Prototype ready"

const HERE = dirname(fileURLToPath(import.meta.url))
const COMPONENT_SRC = readFileSync(
  join(HERE, "..", "DesignAgentNotificationReplay.tsx"),
  "utf8",
)

function makeSessionStorage(): Storage {
  let store: Record<string, string> = {}
  return {
    get length(): number {
      return Object.keys(store).length
    },
    getItem: (k: string): string | null => (k in store ? store[k] : null),
    setItem: (k: string, v: string): void => {
      store[k] = String(v)
    },
    removeItem: (k: string): void => {
      delete store[k]
    },
    clear: (): void => {
      store = {}
    },
    key: (i: number): string | null => Object.keys(store)[i] ?? null,
  }
}

const testGlobal = globalThis as unknown as {
  window?: { sessionStorage: Storage }
}
function installStorage() {
  testGlobal.window = { sessionStorage: makeSessionStorage() }
}
function removeWindow() {
  testGlobal.window = undefined
}

afterEach(() => {
  removeWindow()
  __resetPageLoadGuards()
  vi.restoreAllMocks()
})

describe("DesignAgentNotificationReplay — renders null (AC7)", () => {
  it("SSR output is empty — no markup (test_replay_component_ssr_renders_null)", () => {
    const html = renderToStaticMarkup(
      React.createElement(DesignAgentNotificationReplay),
    )
    expect(html).toBe("")
  })
})

describe("mount effect calls both replay and resume (Part C, AC14)", () => {
  it("test_replay_component_effect_calls_both_replay_and_resume — source-level pin (effects don't fire under SSR render)", () => {
    expect(COMPONENT_SRC).toContain("replayCompletedNotifications(showToast)")
    expect(COMPONENT_SRC).toContain("resumePendingNotifications(showToast)")
  })
})

describe("shell replay path (AC1) — fires without the drawer", () => {
  it("shows a completed entry's toast via the shell path (test_replay_fires_on_shell_mount_without_drawer)", () => {
    installStorage()
    markCompleted(7, "Open the PRD's Design section to view it.")
    const showToast = vi.fn()
    // The shell component delegates to this exact call in its mount effect — no
    // drawer involved (before P6-05 the replay lived only in the drawer).
    replayCompletedNotifications(showToast)
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith(
      READY,
      "Open the PRD's Design section to view it.",
    )
  })
})

describe("acked-until-user-acks (AC3) — no auto-ack on first show", () => {
  it("does NOT remove the entry on show; a reload re-shows (test_replay_does_not_auto_ack_on_first_show)", () => {
    installStorage()
    markCompleted(3, "sub3")
    const showToast = vi.fn()
    replayCompletedNotifications(showToast)
    // Shown — but NOT acknowledged: the sessionStorage entry survives.
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(pendingCompleted()).toEqual([
      { prototypeId: 3, sub: "sub3", prdId: null },
    ])
    // Same page-load: a second replay does NOT re-show (per-page-load guard).
    const showToast2 = vi.fn()
    replayCompletedNotifications(showToast2)
    expect(showToast2).not.toHaveBeenCalled()
    // A simulated hard reload (guards reset) re-shows it again — until acked.
    __resetPageLoadGuards()
    const showToast3 = vi.fn()
    replayCompletedNotifications(showToast3)
    expect(showToast3).toHaveBeenCalledTimes(1)
  })
})

describe("ack-on-toast-clear precision (AC3 / AC11)", () => {
  it("acks the replay's own last-shown id when its toast clears (test_ack_on_toast_clear_clears_entry)", () => {
    installStorage()
    markCompleted(9, "sub9")
    replayCompletedNotifications(vi.fn())
    // The toast slot held the replay's emission; it then auto-hides (→ null).
    const ackId = shouldAckOnClear(
      { title: READY, sub: "sub9" },
      null,
      getLastReplayShow(),
    )
    expect(ackId).toBe(9)
    acknowledge(ackId as number)
    expect(pendingCompleted()).toEqual([])
  })

  it("does NOT ack when a competing toast supplanted the slot (test_ack_only_last_shown_id_on_clear)", () => {
    installStorage()
    markCompleted(9, "sub9")
    replayCompletedNotifications(vi.fn())
    // A different feature's toast occupied the slot; its clear must NOT ack the
    // replay's still-pending id (the cleared toast doesn't match the last show).
    const ackId = shouldAckOnClear(
      { title: "Workspace saved", sub: "All set" },
      null,
      getLastReplayShow(),
    )
    expect(ackId).toBeNull()
    // The replay's entry is untouched — it waits for a clear of its OWN toast.
    expect(pendingCompleted()).toEqual([
      { prototypeId: 9, sub: "sub9", prdId: null },
    ])
  })

  it("does NOT ack on a non-clear transition (prev null, or current still set)", () => {
    installStorage()
    markCompleted(9, "sub9")
    replayCompletedNotifications(vi.fn())
    const last = getLastReplayShow()
    // prev null → no clear happened
    expect(shouldAckOnClear(null, null, last)).toBeNull()
    // current still set → the toast is still showing (not cleared)
    expect(
      shouldAckOnClear({ title: READY, sub: "sub9" }, { title: "x", sub: "y" }, last),
    ).toBeNull()
  })
})
