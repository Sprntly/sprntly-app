import { describe, expect, it } from "vitest"

import { reportTokenFromPathname } from "../reportTokenFromPathname"

describe("reportTokenFromPathname", () => {
  it("reads the token from a /r/<token> path", () => {
    expect(reportTokenFromPathname("/r/tok-123")).toBe("tok-123")
    expect(reportTokenFromPathname("/r/tok-123/")).toBe("tok-123")
  })

  it("honours a base path", () => {
    expect(reportTokenFromPathname("/demo/r/tok-9/", "/demo")).toBe("tok-9")
  })

  it("decodes an encoded segment", () => {
    expect(reportTokenFromPathname("/r/a%2Fb")).toBe("a/b")
  })

  it("returns null for the prerender sentinel", () => {
    // /r/_.html is the static-export shell; sending "_" to the API as a token
    // would be a live lookup for a value we never mint.
    expect(reportTokenFromPathname("/r/_")).toBeNull()
  })

  it("returns null off the /r subtree or with no token", () => {
    expect(reportTokenFromPathname("/about")).toBeNull()
    expect(reportTokenFromPathname("/r")).toBeNull()
    expect(reportTokenFromPathname("/")).toBeNull()
    expect(reportTokenFromPathname("/p/acme/tok-1")).toBeNull()
  })

  it("returns null for a malformed escape rather than throwing", () => {
    expect(reportTokenFromPathname("/r/%E0%A4%A")).toBeNull()
  })
})
