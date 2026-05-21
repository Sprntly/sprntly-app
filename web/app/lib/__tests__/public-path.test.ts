import { afterEach, describe, expect, it, vi } from "vitest"
import { getBasePath, publicAbsoluteUrl, publicPath } from "../public-path"

describe("publicPath", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("returns path unchanged without base path", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "")
    expect(publicPath("/terms")).toBe("/terms")
  })

  it("prefixes with /demo when configured", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/demo")
    expect(publicPath("/terms")).toBe("/demo/terms")
    expect(getBasePath()).toBe("/demo")
  })

  it("builds absolute URL for legal links", () => {
    vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/demo")
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://api.sprntly.ai")
    expect(publicAbsoluteUrl("/terms")).toBe("https://api.sprntly.ai/demo/terms")
  })
})
