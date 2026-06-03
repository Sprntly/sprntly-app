import { afterEach, describe, expect, it } from "vitest"
import {
  acknowledge,
  markCompleted,
  markPending,
  pendingCompleted,
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
