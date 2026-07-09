// Regression lock for the prod static-export token bug.
//
// Under output:"export" the `/p/[slug]/[token]` + `/p/[slug]` routes are
// prerendered under the "_" sentinel and nginx rewrites every real /p URL to that
// one static file, so useParams() returns "_" on the client — never the real
// token in the address bar. These pure helpers derive the real token from
// window.location.pathname instead. This test proves they extract the REAL token
// from a realistic pathname and reject the sentinel — the exact failure that
// shipped (by-token/_ → "Could not load this prototype").
import { describe, it, expect } from "vitest"
import {
  publicPathSegments,
  shareTokenFromPathname,
  shareTokenFromLocation,
} from "../shareTokenFromPathname"

describe("publicPathSegments", () => {
  it("returns the segments after the leading /p", () => {
    expect(publicPathSegments("/p/acme/tok-123")).toEqual(["acme", "tok-123"])
    expect(publicPathSegments("/p/tok-9")).toEqual(["tok-9"])
  })

  it("is trailing-slash tolerant", () => {
    expect(publicPathSegments("/p/acme/tok-123/")).toEqual(["acme", "tok-123"])
    expect(publicPathSegments("/p/tok-9/")).toEqual(["tok-9"])
  })

  it("strips a NEXT_PUBLIC_BASE_PATH prefix (with or without a trailing slash)", () => {
    expect(publicPathSegments("/demo/p/acme/tok-123", "/demo")).toEqual(["acme", "tok-123"])
    expect(publicPathSegments("/demo/p/tok-9/", "/demo/")).toEqual(["tok-9"])
  })

  it("returns [] for paths that are not under /p", () => {
    expect(publicPathSegments("/about")).toEqual([])
    expect(publicPathSegments("/")).toEqual([])
    // A non-matching base path is simply ignored; the path is still under /p.
    expect(publicPathSegments("/p/acme/tok", "/demo")).toEqual(["acme", "tok"])
    // A path that lives under the base path but is NOT a /p route → [].
    expect(publicPathSegments("/demo/about", "/demo")).toEqual([])
  })
})

describe("shareTokenFromPathname", () => {
  it("derives the REAL token from the canonical /p/<slug>/<token> URL", () => {
    expect(shareTokenFromPathname("/p/acme/tok-abc123")).toBe("tok-abc123")
  })

  it("derives the token from the legacy 1-segment /p/<token> URL", () => {
    expect(shareTokenFromPathname("/p/tok-legacy")).toBe("tok-legacy")
  })

  it("derives the REAL token from the 3-segment /p/<company>/<feature>/<token> URL (AC9)", () => {
    // The token is always the LAST /p segment regardless of depth, so the new
    // 3-segment canonical route resolves the same token as the 2-segment one —
    // no change to the depth-agnostic helper needed.
    expect(shareTokenFromPathname("/p/acme/onboarding-revamp/tok-abc123")).toBe(
      "tok-abc123",
    )
    // Same token, 2-seg vs 3-seg depth → identical resolution.
    expect(shareTokenFromPathname("/p/acme/tok-abc123")).toBe(
      shareTokenFromPathname("/p/acme/onboarding-revamp/tok-abc123"),
    )
  })

  it("returns null for the 3-segment prerender sentinel (/p/_/_/_.html)", () => {
    expect(shareTokenFromPathname("/p/_/_/_")).toBeNull()
  })

  it("returns null for the prerender sentinel (the bug: never resolve by-token/_)", () => {
    // /p/_/_.html (2-seg sentinel) and /p/_.html (1-seg sentinel).
    expect(shareTokenFromPathname("/p/_/_")).toBeNull()
    expect(shareTokenFromPathname("/p/_")).toBeNull()
  })

  it("is base-path aware and trailing-slash tolerant", () => {
    expect(shareTokenFromPathname("/demo/p/acme/tok-xyz", "/demo")).toBe("tok-xyz")
    expect(shareTokenFromPathname("/p/acme/tok-xyz/")).toBe("tok-xyz")
  })

  it("URL-decodes the token segment", () => {
    expect(shareTokenFromPathname("/p/acme/tok%2Dwith%2Ddashes")).toBe("tok-with-dashes")
  })

  it("returns null for an empty / non-share path", () => {
    expect(shareTokenFromPathname("/")).toBeNull()
    expect(shareTokenFromPathname("/about")).toBeNull()
  })

  it("returns null for a malformed percent-escape rather than throwing", () => {
    expect(shareTokenFromPathname("/p/acme/%E0%A4%A")).toBeNull()
  })
})

describe("shareTokenFromLocation", () => {
  it("returns null on the server (no window) — the node-env vitest run has no window", () => {
    // This file runs in the default node environment (no jsdom), so `window` is
    // undefined here, exercising the SSR guard. The real client read is covered by
    // the shareTokenFromPathname cases above + the tester's live static-export pass.
    expect(typeof window).toBe("undefined")
    expect(shareTokenFromLocation()).toBeNull()
  })
})
