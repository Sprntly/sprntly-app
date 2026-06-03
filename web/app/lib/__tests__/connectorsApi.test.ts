import { afterEach, describe, expect, it, vi } from "vitest"
import { API_URL, connectorsApi } from "../api"

describe("connectorsApi", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("builds google drive authorize URL with dataset", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl("acme")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?dataset=acme`,
    )
  })

  it("encodes dataset slug", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl("a b/c")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?dataset=a%20b%2Fc`,
    )
  })

  it("builds figma authorize URL with no params", () => {
    expect(connectorsApi.figmaAuthorizeUrl()).toBe(
      `${API_URL}/v1/connectors/figma/authorize`,
    )
  })

  it("builds github authorize URL with no params", () => {
    expect(connectorsApi.githubAuthorizeUrl()).toBe(
      `${API_URL}/v1/connectors/github/authorize`,
    )
  })

  // In a browser the surface's base URL rides along as return_to so the OAuth
  // callback comes back to app vs demo correctly (not a single FRONTEND_URL).
  it("appends return_to from the current surface for figma", () => {
    vi.stubGlobal("window", { location: { origin: "https://app.sprntly.ai" } })
    expect(connectorsApi.figmaAuthorizeUrl()).toBe(
      `${API_URL}/v1/connectors/figma/authorize?return_to=${encodeURIComponent("https://app.sprntly.ai")}`,
    )
  })

  it("appends return_to with & when the URL already has a query (drive)", () => {
    vi.stubGlobal("window", { location: { origin: "https://app.sprntly.ai" } })
    expect(connectorsApi.googleDriveAuthorizeUrl("acme")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?dataset=acme&return_to=${encodeURIComponent("https://app.sprntly.ai")}`,
    )
  })
})
