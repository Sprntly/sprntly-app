import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { API_URL, connectorsApi } from "../api"

// Post-require_company refactor: routes resolve the active company
// entirely from the JWT, so connectorsApi methods take no tenant arg.
// Tests below pin the new (clean) URL shapes.

describe("connectorsApi authorize URLs", () => {
  it("builds google drive authorize URL with just dataset", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl("acme")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?dataset=acme`,
    )
  })

  it("encodes the dataset slug", () => {
    expect(connectorsApi.googleDriveAuthorizeUrl("a b/c")).toBe(
      `${API_URL}/v1/connectors/google-drive/authorize?dataset=a%20b%2Fc`,
    )
  })

  it("figma authorize URL is parameter-free (company resolved server-side)", () => {
    expect(connectorsApi.figmaAuthorizeUrl()).toBe(
      `${API_URL}/v1/connectors/figma/authorize`,
    )
  })

  it("github authorize URL is parameter-free", () => {
    expect(connectorsApi.githubAuthorizeUrl()).toBe(
      `${API_URL}/v1/connectors/github/authorize`,
    )
  })
})

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

  it("POSTs to /v1/connectors/{provider}/start-oauth with no query string", async () => {
    await connectorsApi.startOauth("figma")
    expect(lastCall).not.toBeNull()
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/figma/start-oauth`)
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init?.body ?? "{}"))).toEqual({})
  })

  it("POSTs the dataset in the body when provided (Google Drive)", async () => {
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

  it("includes return_to in the body when provided (3rd positional arg)", async () => {
    await connectorsApi.startOauth("figma", undefined, "/onboarding/4")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      return_to: "/onboarding/4",
    })
  })

  it("includes both dataset and return_to when both are provided", async () => {
    await connectorsApi.startOauth("google_drive", "meridian", "/onboarding/4")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      dataset: "meridian",
      return_to: "/onboarding/4",
    })
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

  it("listSlackChannels hits /slack/channels with no query string", async () => {
    await connectorsApi.listSlackChannels()
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/slack/channels`)
    expect(lastCall!.init?.method ?? "GET").toBe("GET")
  })

  it("setSlackConfig POSTs channel_id + channel_name in body only", async () => {
    await connectorsApi.setSlackConfig("C123", "product-launches")
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/slack/config`)
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({
      channel_id: "C123",
      channel_name: "product-launches",
    })
  })

  it("setSlackConfig omits channel_name when not provided", async () => {
    await connectorsApi.setSlackConfig("C123")
    const body = JSON.parse(String(lastCall!.init!.body))
    expect(body.channel_id).toBe("C123")
    expect(body.channel_name).toBeUndefined()
  })

  it("disconnectSlack DELETEs the bare endpoint", async () => {
    await connectorsApi.disconnectSlack()
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/slack`)
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

  it("POSTs to the bare /v1/connectors/{provider}/test endpoint", async () => {
    await connectorsApi.testConnection("figma")
    expect(lastCall!.url).toBe(`${API_URL}/v1/connectors/figma/test`)
  })
})
