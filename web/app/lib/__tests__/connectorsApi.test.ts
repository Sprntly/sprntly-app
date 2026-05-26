import { describe, expect, it } from "vitest"
import { API_URL, connectorsApi } from "../api"

describe("connectorsApi", () => {
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
})
