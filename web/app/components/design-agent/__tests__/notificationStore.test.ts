import { afterEach, describe, expect, it } from "vitest"
import {
  __resetPageLoadGuards,
  acknowledge,
  getLastReplayShow,
  markCancelled,
  markCompleted,
  markPending,
  markSeenThisLoad,
  pendingCompleted,
  recordReplayShow,
  shouldAckOnClear,
  wasCancelled,
  wasSeenThisLoad,
} from "../notificationStore"

/**
 * P5-09 — notificationStore unit tests. The repo's vitest env is `node` (no
 * jsdom), so `window` is undefined by default. We install a tiny in-memory
 * `sessionStorage` on `globalThis.window` for the persistence cases and remove
 * it for the SSR no-op case (AC6).
 */

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

// `unknown as` deliberately breaks the lib.dom `Window` typing so we can install
// a node-env stub and set it back to undefined (the SSR / no-storage case).
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
})

describe("notificationStore", () => {
  it("markCompleted persists a completed entry (AC1)", () => {
    installStorage()
    markCompleted(7, "Open it")
    expect(pendingCompleted()).toEqual([{ prototypeId: 7, sub: "Open it" }])
  })

  it("pendingCompleted returns only completed entries — pending is excluded (AC3)", () => {
    installStorage()
    markPending(1)
    markCompleted(2, "ready 2")
    expect(pendingCompleted()).toEqual([{ prototypeId: 2, sub: "ready 2" }])
  })

  it("markCompleted flips an existing pending entry to completed (no duplicate)", () => {
    installStorage()
    markPending(5)
    markCompleted(5, "now ready")
    const completed = pendingCompleted()
    expect(completed).toEqual([{ prototypeId: 5, sub: "now ready" }])
    expect(completed).toHaveLength(1)
  })

  it("acknowledge removes the entry so it is no longer pending-completed (AC2)", () => {
    installStorage()
    markCompleted(3, "ready 3")
    acknowledge(3)
    expect(pendingCompleted()).toEqual([])
  })

  it("entries are keyed per prototype — acknowledging one leaves the other (AC4)", () => {
    installStorage()
    markCompleted(1, "a")
    markCompleted(2, "b")
    acknowledge(1)
    expect(pendingCompleted()).toEqual([{ prototypeId: 2, sub: "b" }])
  })

  it("no-ops and returns empty when sessionStorage is unavailable (AC6)", () => {
    removeWindow()
    expect(() => {
      markPending(1)
      markCompleted(1, "x")
      acknowledge(1)
    }).not.toThrow()
    expect(pendingCompleted()).toEqual([])
  })

  it("tolerates malformed JSON in storage and returns empty", () => {
    installStorage()
    testGlobal.window!.sessionStorage.setItem(
      "design-agent:notifications",
      "{not json",
    )
    expect(pendingCompleted()).toEqual([])
  })
})

// ─── P6-05: per-page-load guard + Decision-D(b) ack precision ────────────────

describe("per-page-load guard (P6-05)", () => {
  it("markSeenThisLoad / wasSeenThisLoad track ids only within a page-load", () => {
    expect(wasSeenThisLoad(7)).toBe(false)
    markSeenThisLoad(7)
    expect(wasSeenThisLoad(7)).toBe(true)
    // A distinct id is independent.
    expect(wasSeenThisLoad(8)).toBe(false)
  })

  it("the guard does NOT remove the sessionStorage entry — only acknowledge does (AC3)", () => {
    installStorage()
    markCompleted(5, "ready 5")
    markSeenThisLoad(5)
    // Seen-this-load is in-memory only; the persisted entry survives a reload.
    expect(pendingCompleted()).toEqual([{ prototypeId: 5, sub: "ready 5" }])
    acknowledge(5)
    expect(pendingCompleted()).toEqual([])
  })

  it("__resetPageLoadGuards clears the guard (simulating a reload re-show)", () => {
    markSeenThisLoad(5)
    expect(wasSeenThisLoad(5)).toBe(true)
    __resetPageLoadGuards()
    expect(wasSeenThisLoad(5)).toBe(false)
  })
})

describe("cancel-aware failure suppression", () => {
  it("markCancelled makes wasCancelled return true for that id only", () => {
    expect(wasCancelled(42)).toBe(false)
    markCancelled(42)
    expect(wasCancelled(42)).toBe(true)
    // An un-marked id is unaffected — a genuine failure never marks the id.
    expect(wasCancelled(43)).toBe(false)
  })

  it("__resetPageLoadGuards clears the cancelled-ids registry (fresh page-load)", () => {
    markCancelled(42)
    expect(wasCancelled(42)).toBe(true)
    __resetPageLoadGuards()
    expect(wasCancelled(42)).toBe(false)
  })
})

describe("Decision-D(b) ack-on-toast-clear precision (P6-05)", () => {
  it("records the last replay show and acks it when its own toast clears (AC11)", () => {
    recordReplayShow(9, "Prototype ready", "sub9")
    expect(getLastReplayShow()).toEqual({
      prototypeId: 9,
      title: "Prototype ready",
      sub: "sub9",
    })
    const ackId = shouldAckOnClear(
      { title: "Prototype ready", sub: "sub9" },
      null,
      getLastReplayShow(),
    )
    expect(ackId).toBe(9)
  })

  it("does NOT ack when a competing toast (different title/sub) cleared (AC11)", () => {
    recordReplayShow(9, "Prototype ready", "sub9")
    expect(
      shouldAckOnClear(
        { title: "Something else", sub: "other" },
        null,
        getLastReplayShow(),
      ),
    ).toBeNull()
  })

  it("does NOT ack on a non-clear (prev null, or current still set, or no last show)", () => {
    recordReplayShow(9, "Prototype ready", "sub9")
    const last = getLastReplayShow()
    expect(shouldAckOnClear(null, null, last)).toBeNull()
    expect(
      shouldAckOnClear({ title: "Prototype ready", sub: "sub9" }, { title: "a", sub: "b" }, last),
    ).toBeNull()
    expect(shouldAckOnClear({ title: "Prototype ready", sub: "sub9" }, null, null)).toBeNull()
  })

  it("the LAST show wins after a multi-entry replay (only the last occupies the slot)", () => {
    recordReplayShow(1, "Prototype ready", "a")
    recordReplayShow(2, "Prototype ready", "b")
    // Slot holds the last show (id 2); a clear matching id 1's sub does NOT ack.
    expect(
      shouldAckOnClear({ title: "Prototype ready", sub: "a" }, null, getLastReplayShow()),
    ).toBeNull()
    expect(
      shouldAckOnClear({ title: "Prototype ready", sub: "b" }, null, getLastReplayShow()),
    ).toBe(2)
  })
})
