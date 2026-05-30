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

import { resolveToken, nextViewerState } from "../PublicTokenViewer"
import { PrototypeViewer } from "../../../components/design-agent/PrototypeViewer"
import { CompletionBar } from "../../../components/design-agent/CompletionBar"
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
  it("returns the resolved view on a 200", async () => {
    mockFetch({
      status: 200,
      body: {
        share_mode: "public",
        requires_passcode: false,
        bundle_url: "https://cdn.example/p/abc/index.html",
        is_complete: true,
      },
    })
    expect(await resolveToken("tok")).toEqual({
      share_mode: "public",
      requires_passcode: false,
      bundle_url: "https://cdn.example/p/abc/index.html",
      is_complete: true,
    })
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
    })
    expect(state).toEqual({
      kind: "ready",
      bundleUrl: "https://cdn.example/p/abc/index.html",
      isComplete: true,
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
    expect(html).toContain('sandbox="allow-scripts allow-same-origin"')
  })

  it("passcode mode with null bundle_url → passcode gate", () => {
    expect(
      nextViewerState({
        share_mode: "passcode",
        requires_passcode: true,
        bundle_url: null,
        is_complete: false,
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

// P2-10: the public viewer mounts a read-only CompletionBar as the chrome —
// a status badge only, no prototypeId, no mutation affordances (AC19, AC3).
describe("P2-10 read-only CompletionBar chrome mount (AC19)", () => {
  it("renders the read-only complete badge inside the chrome slot", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: true,
        chrome: React.createElement(CompletionBar, {
          isComplete: true,
          editable: false,
        }),
      }),
    )
    expect(html).toContain('data-testid="prototype-chrome"')
    expect(html).toContain('data-testid="completion-bar-readonly"')
    expect(html).toContain("Marked Complete")
    // No mutating affordances leak into the public viewer.
    expect(html).not.toContain('data-testid="mark-complete-btn"')
    expect(html).not.toContain('data-testid="resume-btn"')
  })

  it("renders the read-only WIP badge for an incomplete prototype", () => {
    const html = renderToStaticMarkup(
      React.createElement(PrototypeViewer, {
        bundleUrl: "https://cdn.example/p/abc/index.html",
        isComplete: false,
        chrome: React.createElement(CompletionBar, {
          isComplete: false,
          editable: false,
        }),
      }),
    )
    expect(html).toContain('data-testid="completion-bar-readonly"')
    expect(html).toContain("Work in progress")
  })
})
