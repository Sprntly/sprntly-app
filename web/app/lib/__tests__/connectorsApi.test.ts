import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { API_URL, connectorsApi } from "../api"

const WS = "ws-uuid-acme"

describe("connectorsApi authorize URLs", () => {
  it("builds google drive authorize URL with workspace + dataset", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl(WS, "acme")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?workspace_id=${WS}&dataset=acme`,
    )
  })

  it("encodes the dataset slug", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl(WS, "a b/c")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?workspace_id=${WS}&dataset=a%20b%2Fc`,
    )
  })

  it("builds figma authorize URL with workspace_id only", () => {
    expect(connectorsApi.figmaAuthorizeUrl(WS)).toBe(
      `${API_URL}/v1/connectors/figma/authorize?workspace_id=${WS}`,
    )
  })

  it("builds github authorize URL with workspace_id only", () => {
    expect(connectorsApi.githubAuthorizeUrl(WS)).toBe(
      `${API_URL}/v1/connectors/github/authorize?workspace_id=${WS}`,
    )
  })

  it("URL-encodes the workspace_id (safety against pathological ids)", () => {
    expect(connectorsApi.figmaAuthorizeUrl("ws with space")).toBe(
      `${API_URL}/v1/connectors/figma/authorize?workspace_id=ws%20with%20space`,
    )
  })
})

// Fetch-driven endpoints — workspace_id always rides as a query param so
// the backend's require_workspace_membership dep can validate it before
// the route body runs.
describe("connectorsApi.startOauth", () => {
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

  it("POSTs to /v1/connectors/{provider}/start-oauth with workspace_id query", async () => {
    await connectorsApi.startOauth(WS, "figma")
    expect(lastCall).not.toBeNull()
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/figma/start-oauth?workspace_id=${WS}`,
    )
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init?.body ?? "{}"))).toEqual({})
  })

  it("POSTs the dataset slug in the body when provided (Google Drive)", async () => {
    await connectorsApi.startOauth(WS, "google_drive", "meridian")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/google_drive/start-oauth?workspace_id=${WS}`,
    )
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      dataset: "meridian",
    })
  })

  it("URL-encodes the provider segment", async () => {
    await connectorsApi.startOauth(WS, "weird/provider")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/weird%2Fprovider/start-oauth?workspace_id=${WS}`,
    )
  })

  it("returns the authorize_url from the response", async () => {
    const res = await connectorsApi.startOauth(WS, "figma")
    expect(res.authorize_url).toBe("https://example.com/auth")
  })
})

describe("connectorsApi Slack methods", () => {
  let originalFetch: typeof globalThis.fetch
  let lastCall: { url: string; init: RequestInit | undefined } | null

  beforeEach(() => {
    originalFetch = globalThis.fetch
    lastCall = null
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      lastCall = { url: String(url), init }
      return new Response(
        JSON.stringify({ channels: [], ok: true, config: {} }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }) as typeof globalThis.fetch
  })
  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("listSlackChannels hits /slack/channels with workspace_id", async () => {
    await connectorsApi.listSlackChannels(WS)
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/slack/channels?workspace_id=${WS}`,
    )
    expect(lastCall!.init?.method ?? "GET").toBe("GET")
  })

  it("setSlackConfig POSTs channel_id + channel_name with workspace_id", async () => {
    await connectorsApi.setSlackConfig(WS, "C123", "product-launches")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/slack/config?workspace_id=${WS}`,
    )
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      channel_id: "C123",
      channel_name: "product-launches",
    })
  })

  it("setSlackConfig omits channel_name when not provided (key sent as undefined → stripped)", async () => {
    await connectorsApi.setSlackConfig(WS, "C123")
    const body = JSON.parse(String(lastCall!.init!.body))
    expect(body.channel_id).toBe("C123")
    // The api client may serialize undefined as missing; just confirm
    // no spurious value sneaks in.
    expect(body.channel_name).toBeUndefined()
  })

  it("disconnectSlack DELETEs with workspace_id", async () => {
    await connectorsApi.disconnectSlack(WS)
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/slack?workspace_id=${WS}`,
    )
    expect(lastCall!.init?.method).toBe("DELETE")
  })
})

describe("connectorsApi.testConnection", () => {
  let originalFetch: typeof globalThis.fetch
  let lastCall: { url: string; init: RequestInit | undefined } | null

  beforeEach(() => {
    originalFetch = globalThis.fetch
    lastCall = null
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      lastCall = { url: String(url), init }
      return new Response(
        JSON.stringify({ ok: true, account_label: "a@b.com", tested_at: "t" }),
        { status: 200, headers: { "content-type": "application/json" } },
      )
    }) as typeof globalThis.fetch
  })
  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("POSTs with workspace_id on the query string", async () => {
    await connectorsApi.testConnection(WS, "figma")
    expect(lastCall!.url).toBe(
      `${API_URL}/v1/connectors/figma/test?workspace_id=${WS}`,
    )
  })
})
