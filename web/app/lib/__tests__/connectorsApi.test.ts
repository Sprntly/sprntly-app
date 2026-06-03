import { afterEach, beforeEach, describe, expect, it } from "vitest"
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

// Commit F: fetch-friendly start-OAuth that returns the URL as JSON so
// the Connect button can attach a Bearer header before navigating.
describe("connectorsApi.startOauth (commit F)", () => {
  let originalFetch: typeof globalThis.fetch
  let lastCall: { url: string; init: RequestInit | undefined } | null

  beforeEach(() => {
    originalFetch = globalThis.fetch
    lastCall = null
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      lastCall = { url: String(url), init }
      return new Response(
        JSON.stringify({ authorize_url: "https://example.com/auth" }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }) as typeof globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("POSTs to /v1/connectors/{provider}/start-oauth with empty body when no dataset", async () => {
    await connectorsApi.startOauth("figma")
    expect(lastCall).not.toBeNull()
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/figma/start-oauth`)
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init?.body ?? "{}"))).toEqual({})
  })

  it("POSTs the dataset slug in the body when provided (Google Drive)", async () => {
    await connectorsApi.startOauth("google_drive", "meridian")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/google_drive/start-oauth`,
    )
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      dataset: "meridian",
    })
  })

  it("URL-encodes the provider segment", async () => {
    await connectorsApi.startOauth("weird/provider")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/weird%2Fprovider/start-oauth`,
    )
  })

  it("returns the authorize_url from the response", async () => {
    const res = await connectorsApi.startOauth("figma")
    expect(res.authorize_url).toBe("https://example.com/auth")
  })
})
