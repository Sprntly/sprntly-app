// Tests for the public `/p/<token>` viewer (P2-05): the client viewer's
// token-resolver + branch logic (PublicTokenViewer), the PasscodeGate logic +
// presentational states, and the PrototypeViewer chrome slot. Node-env vitest
// (no DOM, no router, no testing-library), so — following the DesignAgentDrawer
// convention — we render markup via renderToStaticMarkup and unit-test the
// extracted pure functions (resolveToken / nextViewerState / submitPasscode)
// with a mocked fetch. PublicTokenViewer itself uses useParams()/useEffect and
// is exercised in P2-11's E2E, not here.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses
// the classic runtime, so expose React globally (PrdSections/DesignAgentDrawer
// test convention) rather than touch the shared vitest config.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { nextViewerState } from "../PublicTokenViewer"
import { resolveToken } from "../resolveToken"
import { legacyRedirectTarget } from "../LegacyTokenRedirect"
import { pathDepthKind } from "../PublicPathRouter"
import { generateStaticParams as catchAllStaticParams } from "../[...segments]/page"
import { PrototypeViewer } from "../../components/design-agent/PrototypeViewer"
import { PasscodeGateView, submitPasscode } from "../PasscodeGate"

function mockFetch(res: { status: number; ok?: boolean; body?: unknown }) {
  const fn = vi.fn().mockResolvedValue({
    status: res.status,
    ok: res.ok ?? (res.status >= 200 && res.status < 300),
    json: async () => res.body ?? {},
  })
  vi.stubGlobal("fetch", fn)
  return fn
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe("resolveToken", () => {
  it("returns the resolved view on a 200, parsing company_slug + cosmetic segments", async () => {
    mockFetch({
      status: 200,
      body: {
        share_mode: "public",
        requires_passcode: false,
        bundle_url: "https://cdn.example/p/abc/index.html",
        is_complete: true,
        company_slug: "sprntly",
        company_display_slug: "lab-x",
        feature_slug: "customer-onboarding-revamp",
        target_platform: "mobile",
      },
    })
    expect(await resolveToken("tok")).toEqual({
      share_mode: "public",
      requires_passcode: false,
      bundle_url: "https://cdn.example/p/abc/index.html",
      is_complete: true,
      company_slug: "sprntly",
      company_display_slug: "lab-x",
      feature_slug: "customer-onboarding-revamp",
      target_platform: "mobile",
    })
  })

  it("defaults company_slug to '' when the backend omits it", async () => {
    mockFetch({
      status: 200,
      body: {
        share_mode: "public",
        requires_passcode: false,
        bundle_url: "https://cdn.example/p/abc/index.html",
        is_complete: true,
      },
    })
    expect((await resolveToken("tok"))?.company_slug).toBe("")
  })

  it("defaults company_display_slug + feature_slug to '' when the backend omits them", async () => {
    mockFetch({
      status: 200,
      body: {
        share_mode: "public",
        requires_passcode: false,
        bundle_url: "https://cdn.example/p/abc/index.html",
        is_complete: true,
        company_slug: "sprntly",
      },
    })
    const view = await resolveToken("tok")
    expect(view?.company_display_slug).toBe("")
    expect(view?.feature_slug).toBe("")
  })

  it("defaults target_platform to 'both' when the backend omits it", async () => {
    mockFetch({
      status: 200,
      body: {
        share_mode: "public",
        requires_passcode: false,
        bundle_url: "https://cdn.example/p/abc/index.html",
        is_complete: true,
        company_slug: "sprntly",
      },
    })
    expect((await resolveToken("tok"))?.target_platform).toBe("both")
  })

  it("returns null on a 404 (→ notFound upstream)", async () => {
    mockFetch({ status: 404 })
    expect(await resolveToken("missing")).toBeNull()
  })

  it("throws on a non-404 resolver error (e.g. a 500) rather than masking it", async () => {
    mockFetch({ status: 500, ok: false })
    await expect(resolveToken("tok")).rejects.toThrow("resolver failed: 500")
  })
})

describe("nextViewerState branch logic", () => {
  it("public mode with a bundle_url → ready (renders the iframe)", () => {
    const state = nextViewerState({
      share_mode: "public",
      requires_passcode: false,
      bundle_url: "https://cdn.example/p/abc/index.html",
      is_complete: true,
      company_slug: "sprntly",
      company_display_slug: "lab-x",
      feature_slug: "customer-onboarding-revamp",
      target_platform: "both",
    })
    expect(state).toEqual({
      kind: "ready",
      bundleUrl: "https://cdn.example/p/abc/index.html",
      isComplete: true,
      targetPlatform: "both",
    })
    // ...and a ready state renders the iframe sourced from the bundle_url.
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: state.kind === "ready" ? state.bundleUrl : "",
        isComplete: false,
      }),
    )
    expect(html).toContain("<iframe")
    expect(html).toContain('src="https://cdn.example/p/abc/index.html"')
    // P6-17 (UX-7): the public /p/<token> viewer shares the single iframe, so it
    // also gains allow-forms (form-centric prototypes must submit on the shared
    // link too); parent-nav / popup tokens stay deliberately omitted.
    expect(html).toContain(
      'sandbox="allow-scripts allow-same-origin allow-forms"',
    )
  })

  it("passcode mode with null bundle_url → passcode gate", () => {
    expect(
      nextViewerState({
        share_mode: "passcode",
        requires_passcode: true,
        bundle_url: null,
        is_complete: false,
        company_slug: "sprntly",
        company_display_slug: "lab-x",
        feature_slug: "customer-onboarding-revamp",
        target_platform: "both",
      }),
    ).toEqual({ kind: "passcode" })
  })

  it("a null view (404) → notfound (the page calls next notFound())", () => {
    expect(nextViewerState(null)).toEqual({ kind: "notfound" })
  })

  it("public mode with no bundle_url → notfound (no empty iframe)", () => {
    expect(
      nextViewerState({
        share_mode: "public",
        requires_passcode: false,
        bundle_url: null,
        is_complete: false,
        company_slug: "sprntly",
        company_display_slug: "lab-x",
        feature_slug: "customer-onboarding-revamp",
        target_platform: "both",
      }),
    ).toEqual({ kind: "notfound" })
  })
})

describe("PasscodeGate", () => {
  it("renders an <input type=password> in the un-verified state", () => {
    const html = renderToStaticMarkup(
      React.createElement(PasscodeGateView, {
        view: null,
        passcode: "",
        error: null,
        busy: false,
        onPasscodeChange: () => {},
        onSubmit: () => {},
      }),
    )
    expect(html).toContain('type="password"')
    expect(html).not.toContain("<iframe")
  })

  it("renders the PrototypeViewer once a verified view is present (success)", () => {
    const html = renderToStaticMarkup(
      React.createElement(PasscodeGateView, {
        view: { bundleUrl: "https://cdn.example/p/xyz/index.html", isComplete: false },
        passcode: "",
        error: null,
        busy: false,
        onPasscodeChange: () => {},
        onSubmit: () => {},
      }),
    )
    expect(html).toContain("<iframe")
    expect(html).toContain('src="https://cdn.example/p/xyz/index.html"')
  })

  it("submitPasscode returns the bundle on a 200", async () => {
    mockFetch({
      status: 200,
      body: { bundle_url: "https://cdn.example/p/xyz/index.html", is_complete: true },
    })
    const result = await submitPasscode({ token: "t", passcode: "hunter2" })
    expect(result).toEqual({
      ok: true,
      bundleUrl: "https://cdn.example/p/xyz/index.html",
      isComplete: true,
      targetPlatform: "both",
    })
  })

  it("shows an 'Incorrect passcode' error on a 401", async () => {
    mockFetch({ status: 401, ok: false })
    const result = await submitPasscode({ token: "t", passcode: "wrong" })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain("Incorrect passcode")
    const html = renderToStaticMarkup(
      React.createElement(PasscodeGateView, {
        view: null,
        passcode: "x",
        error: "Incorrect passcode.",
        busy: false,
        onPasscodeChange: () => {},
        onSubmit: () => {},
      }),
    )
    expect(html).toContain("Incorrect passcode")
  })

  it("shows a 'Too many attempts' message on a 429", async () => {
    mockFetch({ status: 429, ok: false })
    const result = await submitPasscode({ token: "t", passcode: "x" })
    expect(result.ok).toBe(false)
    if (!result.ok) expect(result.error).toContain("Too many attempts")
  })
})

// Sharing model: the legacy `/p/<token>` path now redirects to the canonical
// `/p/<company>/<feature>/<token>` form. A single catch-all route
// ([...segments]/page.tsx) dispatches by REAL path depth (PublicPathRouter),
// not by a per-depth dynamic folder name; legacyRedirectTarget is the pure
// target-computation LegacyTokenRedirect calls after resolving the token (the
// router.replace itself lives in the client component, exercised in E2E).
describe("legacyRedirectTarget (legacy → canonical redirect)", () => {
  it("computes /p/<company>/<feature>/<token> from a resolved view", () => {
    expect(
      legacyRedirectTarget(
        {
          share_mode: "public",
          requires_passcode: false,
          bundle_url: "https://cdn.example/p/abc/index.html",
          is_complete: true,
          company_slug: "sprntly",
          company_display_slug: "lab-x",
          feature_slug: "customer-onboarding-revamp",
          target_platform: "both",
        },
        "abc",
      ),
    ).toBe("/p/lab-x/customer-onboarding-revamp/abc")
  })

  it("falls back to /p/company/prototype/<token> when both cosmetic segments are empty", () => {
    expect(
      legacyRedirectTarget(
        {
          share_mode: "public",
          requires_passcode: false,
          bundle_url: "https://cdn.example/p/abc/index.html",
          is_complete: true,
          company_slug: "sprntly",
          company_display_slug: "",
          feature_slug: "",
          target_platform: "both",
        },
        "abc",
      ),
    ).toBe("/p/company/prototype/abc")
  })

  it("returns null for a 404/null view (the caller calls notFound())", () => {
    expect(legacyRedirectTarget(null, "abc")).toBeNull()
  })
})

// A single catch-all route (/p/[...segments]/page.tsx) serves every /p/...
// depth — the 1-seg legacy bookmark, the 2-seg canonical form, and the 3-seg
// canonical form all resolve through it. generateStaticParams emits one static
// shell per depth we still want a distinct nginx sentinel file for.
describe("catch-all /p/[...segments] route", () => {
  it("generateStaticParams emits the 1-seg, 2-seg, and 3-seg sentinels for static export", () => {
    expect(catchAllStaticParams()).toEqual([
      { segments: ["_"] },
      { segments: ["_", "_"] },
      { segments: ["_", "_", "_"] },
    ])
  })
})

// PublicPathRouter dispatches by the REAL request depth (not by which static
// sentinel Next prerendered) — 1 segment is the legacy bookmark shape; any
// other depth is a canonical share link, rendered inline regardless of how
// many cosmetic segments precede the token.
describe("pathDepthKind (PublicPathRouter dispatch)", () => {
  it("classifies a 1-segment path as legacy", () => {
    expect(pathDepthKind("/p/tok-abc123")).toBe("legacy")
  })

  it("classifies a 2-segment path as canonical", () => {
    expect(pathDepthKind("/p/acme/tok-abc123")).toBe("canonical")
  })

  it("classifies a 3-segment path as canonical", () => {
    expect(pathDepthKind("/p/acme/onboarding-revamp/tok-abc123")).toBe("canonical")
  })

  it("is base-path aware, matching publicPathSegments", () => {
    expect(pathDepthKind("/demo/p/tok-abc123", "/demo")).toBe("legacy")
    expect(pathDepthKind("/demo/p/acme/tok-abc123", "/demo")).toBe("canonical")
  })
})

describe("PrototypeViewer chrome slot (AC9)", () => {
  it("renders the chrome prop inside the always-present chrome slot", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: false,
        chrome: React.createElement("div", { "data-testid": "my-chrome" }),
      }),
    )
    expect(html).toContain('data-testid="prototype-chrome"')
    expect(html).toContain('data-testid="my-chrome"')
  })

  it("keeps the chrome slot present (empty) when chrome is undefined", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: false,
      }),
    )
    expect(html).toContain('data-testid="prototype-chrome"')
  })
})

// Phase-1 chrome cleanup: the public viewer no longer mounts the work-status
// pill (CompletionBar) or the read-only CommentsPanel. The chrome slot is still
// present (ManualEditOverlay mounts but renders nothing without a prototypeId).
// These tests assert the removed elements are ABSENT on the public surface.
describe("Phase-1 chrome cleanup: status pill + comments box absent from public viewer", () => {
  it("does NOT render the completion-bar-readonly pill in the public chrome slot", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: true,
        // No CompletionBar or CommentsPanel passed — matches the cleaned-up
        // PublicTokenViewer chrome slot (Phase 1).
        chrome: React.createElement(React.Fragment, null),
      }),
    )
    expect(html).toContain('data-testid="prototype-chrome"')
    expect(html).not.toContain('data-testid="completion-bar-readonly"')
    expect(html).not.toContain("Marked Complete")
    expect(html).not.toContain("Work in progress")
  })

  it("does NOT render the read-only CommentsPanel in the public chrome slot", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: false,
        chrome: React.createElement(React.Fragment, null),
      }),
    )
    expect(html).toContain('data-testid="prototype-chrome"')
    // The CommentsPanel renders a da-comments-panel root — absent here.
    expect(html).not.toContain("da-comments-panel")
    // The instructional copy that appears in the read-only panel is gone.
    expect(html).not.toContain("Right-click any element")
  })
})
