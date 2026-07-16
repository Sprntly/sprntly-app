// @vitest-environment jsdom
//
// PWA head-tag injection for the public /p viewer (mobile installability).
// The per-prototype manifest is API-served (the app is a static export), and
// PublicTokenViewer injects the <link rel="manifest"> + companion tags into
// document.head ONLY after a token resolves to a READY view — a loading,
// passcode-gated, or 404 state must never expose a manifest link. Tags carry a
// `data-da-pwa` marker so re-application is idempotent (token change replaces)
// and unmount removes them.
//
// PublicPrototypeChrome and PasscodeGate are stubbed: the effect under test
// lives in PublicTokenViewer itself, and the chrome's own behaviour is pinned
// by its dedicated suites.
import * as React from "react"
import * as fs from "node:fs"
import * as path from "node:path"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { resolveTokenMock, notFoundMock } = vi.hoisted(() => ({
  resolveTokenMock: vi.fn(),
  notFoundMock: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND")
  }),
}))

// The real token comes from the live URL; feed a fixed token so the resolver
// effect fires deterministically.
vi.mock("../shareTokenFromPathname", () => ({
  shareTokenFromLocation: () => "tok",
  shareTokenFromPathname: () => "tok",
}))
vi.mock("../resolveToken", () => ({ resolveToken: resolveTokenMock }))
vi.mock("next/navigation", () => ({ notFound: notFoundMock }))
vi.mock("../PasscodeGate", () => ({
  PasscodeGate: () => <div data-testid="gate-stub" />,
}))
vi.mock("../PublicPrototypeChrome", () => ({
  PublicPrototypeChrome: () => <div data-testid="ready-stub" />,
}))

import {
  applyPwaHeadTags,
  PublicTokenViewer,
  removePwaHeadTags,
} from "../PublicTokenViewer"
import { API_URL } from "../../lib/api"

function readyView() {
  return {
    share_mode: "public" as const,
    requires_passcode: false,
    bundle_url: "https://cdn.example/p/abc/index.html",
    is_complete: false,
    company_slug: "acme",
    company_display_slug: "lab-x",
    feature_slug: "onboarding",
    target_platform: "both",
  }
}

const pwaTags = () => Array.from(document.head.querySelectorAll("[data-da-pwa]"))
const manifestLinks = () =>
  Array.from(document.head.querySelectorAll('link[rel="manifest"]'))

// notFound() throws during render; swallow it so the 404 case can be asserted.
class Boundary extends React.Component<
  { children: React.ReactNode },
  { err: boolean }
> {
  state = { err: false }
  static getDerivedStateFromError() {
    return { err: true }
  }
  render() {
    return this.state.err ? <div data-testid="nf-boundary" /> : this.props.children
  }
}

afterEach(() => {
  cleanup()
  removePwaHeadTags() // belt-and-braces: jsdom reuses one document per file
  vi.clearAllMocks()
})

describe("PublicTokenViewer — PWA head injection", () => {
  it("test_head_injection_only_on_ready_state: loading → none", async () => {
    resolveTokenMock.mockReturnValue(new Promise(() => {})) // never resolves
    render(<PublicTokenViewer />)
    await waitFor(() => expect(resolveTokenMock).toHaveBeenCalled())
    expect(pwaTags()).toHaveLength(0)
  })

  it("test_head_injection_only_on_ready_state: passcode gate → none", async () => {
    resolveTokenMock.mockResolvedValue({
      ...readyView(),
      share_mode: "passcode" as const,
      requires_passcode: true,
      bundle_url: null,
    })
    render(<PublicTokenViewer />)
    await waitFor(() => expect(screen.getByTestId("gate-stub")).toBeTruthy())
    expect(pwaTags()).toHaveLength(0)
  })

  it("test_head_injection_only_on_ready_state: 404 → none", async () => {
    resolveTokenMock.mockResolvedValue(null)
    render(
      <Boundary>
        <PublicTokenViewer />
      </Boundary>,
    )
    await waitFor(() => expect(notFoundMock).toHaveBeenCalled())
    expect(pwaTags()).toHaveLength(0)
  })

  it("test_head_injection_only_on_ready_state: ready → all tags exactly once", async () => {
    resolveTokenMock.mockResolvedValue(readyView())
    render(<PublicTokenViewer />)
    await waitFor(() => expect(screen.getByTestId("ready-stub")).toBeTruthy())
    await waitFor(() => expect(manifestLinks()).toHaveLength(1))
    // Every injected tag carries the marker; the full companion set is present once.
    expect(pwaTags()).toHaveLength(4)
    const themeColor = document.head.querySelectorAll('meta[name="theme-color"]')
    expect(themeColor).toHaveLength(1)
    expect(themeColor[0].getAttribute("content")).toBe("#f6f7f6")
    const appleIcon = document.head.querySelectorAll('link[rel="apple-touch-icon"]')
    expect(appleIcon).toHaveLength(1)
    expect(appleIcon[0].getAttribute("href")).toBe("/pwa/prototype-icon-192.png")
    const appleCapable = document.head.querySelectorAll(
      'meta[name="apple-mobile-web-app-capable"]',
    )
    expect(appleCapable).toHaveLength(1)
    expect(appleCapable[0].getAttribute("content")).toBe("yes")
    // Plain manifest link — no crossorigin attribute (no credentials needed; the
    // token is in the URL and CORS middleware covers the app origin).
    expect(manifestLinks()[0].hasAttribute("crossorigin")).toBe(false)
  })

  it("test_manifest_href_targets_api_origin_with_token: exact href", async () => {
    resolveTokenMock.mockResolvedValue(readyView())
    render(<PublicTokenViewer />)
    await waitFor(() => expect(manifestLinks()).toHaveLength(1))
    expect(manifestLinks()[0].getAttribute("href")).toBe(
      `${API_URL}/v1/design-agent/by-token/tok/manifest.webmanifest`,
    )
  })

  it("test_head_injection_idempotent_and_cleaned_up: re-render no dupes; token swap replaces; unmount removes", async () => {
    resolveTokenMock.mockResolvedValue(readyView())
    const { rerender, unmount } = render(<PublicTokenViewer />)
    await waitFor(() => expect(manifestLinks()).toHaveLength(1))
    rerender(<PublicTokenViewer />)
    expect(manifestLinks()).toHaveLength(1)
    expect(pwaTags()).toHaveLength(4)
    // Token swap (legacy redirect → canonical) re-runs the same apply helper the
    // effect uses — the prior instance is replaced, never duplicated.
    applyPwaHeadTags("tok-b")
    expect(manifestLinks()).toHaveLength(1)
    expect(manifestLinks()[0].getAttribute("href")).toBe(
      `${API_URL}/v1/design-agent/by-token/tok-b/manifest.webmanifest`,
    )
    expect(pwaTags()).toHaveLength(4)
    // Unmount removes everything (the effect cleanup owns the marker set).
    unmount()
    expect(pwaTags()).toHaveLength(0)
    expect(manifestLinks()).toHaveLength(0)
  })

  it("test_head_injection_idempotent_and_cleaned_up: token with URL-significant chars is encoded", () => {
    applyPwaHeadTags("a/b?c")
    expect(manifestLinks()[0].getAttribute("href")).toBe(
      `${API_URL}/v1/design-agent/by-token/a%2Fb%3Fc/manifest.webmanifest`,
    )
  })
})

describe("PWA icon assets", () => {
  it("test_pwa_icons_present: both PNGs exist in web/public/pwa with nonzero size", () => {
    const pwaDir = path.resolve(__dirname, "../../../public/pwa")
    for (const name of ["prototype-icon-192.png", "prototype-icon-512.png"]) {
      const p = path.join(pwaDir, name)
      const stat = fs.statSync(p)
      expect(stat.size).toBeGreaterThan(0)
      // PNG magic bytes — a real image, not an empty/placeholder text file.
      const head = fs.readFileSync(p).subarray(0, 8)
      expect(Array.from(head)).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
    }
  })
})
