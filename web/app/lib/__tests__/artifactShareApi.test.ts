import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { API_URL } from "../api"
import {
  artifactShareApi,
  resolveArtifactShare,
  tryAutoJoinCompanyOnDomainMatch,
} from "../artifactShareApi"

describe("artifactShareApi URL construction", () => {
  let originalFetch: typeof globalThis.fetch
  let lastCall: { url: string; init: RequestInit | undefined } | null

  beforeEach(() => {
    originalFetch = globalThis.fetch
    lastCall = null
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      lastCall = { url: String(url), init }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    }) as typeof globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("getMetadata GETs /v1/artifact-share/{token}", async () => {
    await artifactShareApi.getMetadata("abc123")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/abc123`)
    expect(lastCall!.init?.method ?? "GET").toBe("GET")
  })

  it("resolve GETs /v1/artifact-share/{token}/resolve", async () => {
    await artifactShareApi.resolve("abc123")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/abc123/resolve`)
  })

  it("join POSTs to /v1/artifact-share/{token}/join with an empty body", async () => {
    await artifactShareApi.join("abc123")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/abc123/join`)
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({})
  })

  it("URL-encodes a token requiring encoding for getMetadata", async () => {
    await artifactShareApi.getMetadata("a b/c")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/a%20b%2Fc`)
  })

  it("URL-encodes a token requiring encoding for resolve", async () => {
    await artifactShareApi.resolve("a b/c")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/a%20b%2Fc/resolve`)
  })

  it("URL-encodes a token requiring encoding for join", async () => {
    await artifactShareApi.join("a b/c")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/a%20b%2Fc/join`)
  })

  it("autoJoinCompany POSTs to /v1/artifact-share/{token}/auto-join-company with an empty body", async () => {
    await artifactShareApi.autoJoinCompany("abc123")
    expect(lastCall!.url).toBe(`${API_URL}/v1/artifact-share/abc123/auto-join-company`)
    expect(lastCall!.init?.method).toBe("POST")
    expect(JSON.parse(String(lastCall!.init!.body))).toEqual({})
  })
})

describe("tryAutoJoinCompanyOnDomainMatch", () => {
  let originalFetch: typeof globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("returns the joined_company_id on success (a match)", async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ joined_company_id: "co-1" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })) as typeof globalThis.fetch

    expect(await tryAutoJoinCompanyOnDomainMatch("abc123")).toBe("co-1")
  })

  it("returns null on a no-op (no match / already a member)", async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ joined_company_id: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })) as typeof globalThis.fetch

    expect(await tryAutoJoinCompanyOnDomainMatch("abc123")).toBeNull()
  })

  it("returns undefined (never throws) on a network/4xx/5xx failure", async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: "Not found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      })) as typeof globalThis.fetch

    expect(await tryAutoJoinCompanyOnDomainMatch("missing-token")).toBeUndefined()
  })
})

describe("resolveArtifactShare", () => {
  let originalFetch: typeof globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("returns the resolve outcome on success", async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({
          outcome: "guest_view",
          artifact_type: "prd",
          artifact_id: 42,
          owning_company_name: "Acme",
          sharer_name: "Ada",
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      )) as typeof globalThis.fetch

    const result = await resolveArtifactShare("abc123")
    expect(result).toEqual({
      outcome: "guest_view",
      artifact_type: "prd",
      artifact_id: 42,
      owning_company_name: "Acme",
      sharer_name: "Ada",
    })
  })

  it("returns undefined (never throws) on a network/4xx/5xx failure", async () => {
    originalFetch = globalThis.fetch
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ detail: "Not found" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      })) as typeof globalThis.fetch

    const result = await resolveArtifactShare("missing-token")
    expect(result).toBeUndefined()
  })
})
