import { afterEach, describe, expect, it } from "vitest"
import {
  getBacklogPrototypePrdId,
  readBacklogPrototypes,
  recordBacklogPrototype,
} from "../backlogPrototypeStore"

/**
 * backlogPrototypeStore unit tests. The repo's vitest env is `node` (no jsdom),
 * so `window` is undefined by default. We install a tiny in-memory `localStorage`
 * for the persistence cases and remove it for the SSR no-op case.
 */

function makeLocalStorage(): Storage {
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
  window?: { localStorage: Storage }
}

function installStorage() {
  testGlobal.window = { localStorage: makeLocalStorage() }
}
function removeWindow() {
  testGlobal.window = undefined
}

afterEach(() => {
  removeWindow()
})

describe("backlogPrototypeStore", () => {
  it("records a theme → prd_id mapping and reads it back", () => {
    installStorage()
    recordBacklogPrototype("theme-a", 42)
    expect(getBacklogPrototypePrdId("theme-a")).toBe(42)
    expect(readBacklogPrototypes()).toEqual({ "theme-a": 42 })
  })

  it("returns null for a theme with no generated prototype", () => {
    installStorage()
    recordBacklogPrototype("theme-a", 42)
    expect(getBacklogPrototypePrdId("theme-b")).toBeNull()
    expect(getBacklogPrototypePrdId(null)).toBeNull()
    expect(getBacklogPrototypePrdId(undefined)).toBeNull()
  })

  it("overwrites a prior mapping for the same theme (latest generation wins)", () => {
    installStorage()
    recordBacklogPrototype("theme-a", 1)
    recordBacklogPrototype("theme-a", 2)
    expect(getBacklogPrototypePrdId("theme-a")).toBe(2)
  })

  it("keeps multiple themes independent", () => {
    installStorage()
    recordBacklogPrototype("theme-a", 1)
    recordBacklogPrototype("theme-b", 2)
    expect(readBacklogPrototypes()).toEqual({ "theme-a": 1, "theme-b": 2 })
  })

  it("ignores empty theme ids and non-number prd ids", () => {
    installStorage()
    recordBacklogPrototype("", 5)
    // @ts-expect-error — guarding runtime misuse
    recordBacklogPrototype("theme-a", "nope")
    expect(readBacklogPrototypes()).toEqual({})
  })

  it("drops malformed persisted values on read", () => {
    installStorage()
    testGlobal.window!.localStorage.setItem(
      "backlog:prototypes",
      JSON.stringify({ good: 7, bad: "x", alsoBad: null }),
    )
    expect(readBacklogPrototypes()).toEqual({ good: 7 })
  })

  it("no-ops gracefully under SSR (no window)", () => {
    removeWindow()
    expect(() => recordBacklogPrototype("theme-a", 1)).not.toThrow()
    expect(readBacklogPrototypes()).toEqual({})
    expect(getBacklogPrototypePrdId("theme-a")).toBeNull()
  })

  it("survives corrupt JSON in storage", () => {
    installStorage()
    testGlobal.window!.localStorage.setItem("backlog:prototypes", "{not json")
    expect(readBacklogPrototypes()).toEqual({})
  })
})
