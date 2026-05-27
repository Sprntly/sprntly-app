import { describe, expect, it } from "vitest"
import { normalizeProductWebsite, validateProductWebsite } from "../onboarding/product-helpers"

describe("product website", () => {
  it("normalizes bare domains", () => {
    expect(normalizeProductWebsite("acme.com")).toBe("https://acme.com")
  })

  it("allows empty", () => {
    expect(normalizeProductWebsite("")).toBeNull()
    expect(validateProductWebsite("")).toBeNull()
  })

  it("rejects invalid urls", () => {
    expect(validateProductWebsite("not a url")).toMatch(/valid website/i)
  })
})
