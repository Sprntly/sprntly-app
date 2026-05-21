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
})
