import { afterEach, describe, expect, it, vi } from "vitest"
import { DEMO_DEFAULT_COMPANY_SLUG, resolveInitialCompany } from "../useActiveCompany"

describe("resolveInitialCompany", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("prefers workspace slug over query and storage", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialCompany("?company=acme", ls, "sprntly-inc")).toBe("sprntly-inc")
  })

  it("prefers ?company query string", () => {
    expect(resolveInitialCompany("?company=acme", null)).toBe("acme")
  })

  it("falls back to localStorage when no query string", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialCompany(null, ls)).toBe("stored_slug")
  })

  it("query string wins over localStorage", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialCompany("?company=urlwins", ls)).toBe("urlwins")
  })

  it("ignores query strings shorter than 2 chars", () => {
    const ls = { getItem: () => "stored_slug" } as unknown as Storage
    expect(resolveInitialCompany("?company=a", ls)).toBe("stored_slug")
  })

  it("ignores empty localStorage values", () => {
    const ls = { getItem: () => "" } as unknown as Storage
    expect(resolveInitialCompany(null, ls)).toBe(DEMO_DEFAULT_COMPANY_SLUG)
  })

  it("defaults to asurion when no signals", () => {
    expect(resolveInitialCompany(null, null)).toBe(DEMO_DEFAULT_COMPANY_SLUG)
  })

  it("handles malformed query string", () => {
    // Spaces would be percent-encoded in a real URL; raw should still not crash.
    expect(resolveInitialCompany("not a query string", null)).toBe(DEMO_DEFAULT_COMPANY_SLUG)
  })

  it("URL with multiple params returns the company one", () => {
    expect(resolveInitialCompany("?foo=bar&company=acme&baz=qux", null)).toBe("acme")
  })
})
