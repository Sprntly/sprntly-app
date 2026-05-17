import { afterEach, describe, expect, it, vi } from "vitest"
import { resolveInitialDataset } from "../useActiveDataset"

describe("resolveInitialDataset", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("prefers ?dataset query string", () => {
    expect(resolveInitialDataset("?dataset=acme", null)).toBe("acme")
  })

  it("falls back to localStorage when no query string", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialDataset(null, ls)).toBe("stored_slug")
  })

  it("query string wins over localStorage", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialDataset("?dataset=urlwins", ls)).toBe("urlwins")
  })

  it("ignores query strings shorter than 2 chars", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialDataset("?dataset=a", ls)).toBe("stored_slug")
  })

  it("ignores empty localStorage values", () => {
    const ls = { getItem: () => "" } as unknown as Storage
    expect(resolveInitialDataset(null, ls)).toBe("asurion")
  })

  it("defaults to asurion when no signals", () => {
    expect(resolveInitialDataset(null, null)).toBe("asurion")
  })

  it("handles malformed query string", () => {
    // Spaces would be percent-encoded in a real URL; raw should still not crash.
    expect(resolveInitialDataset("not a query string", null)).toBe("asurion")
  })

  it("URL with multiple params returns the dataset one", () => {
    expect(resolveInitialDataset("?foo=bar&dataset=acme&baz=qux", null)).toBe("acme")
  })
})
