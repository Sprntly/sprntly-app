// @vitest-environment jsdom
//
// Integration test for the public single-device viewer treatment. It mounts the
// REAL PublicTokenViewer (not a leaf) with a mocked resolver returning each
// target_platform value, and asserts the container-level behaviour end-to-end:
//
//   - mobile-only / desktop-only → the Desktop/Mobile toggle (aria-label
//     "Preview platform") is NOT rendered, and a static device badge takes its
//     slot; the stage starts in the prototype's own form factor.
//   - both / legacy → the toggle renders as before and NO badge is shown.
//
// Mounting the real container (rather than only the DeviceBadge leaf or a
// hand-composed PrototypeViewer fragment) is deliberate: it proves the
// showDesktop/showMobile/initialPlatform props + the singleDevice badge gate are
// actually threaded through PublicTokenViewer → PrototypeViewer, so a dropped
// prop mid-tree fails here. CommentsPanel is stubbed (it fetches on mount and is
// not under test); the resolver + share-token source are mocked so no network or
// real URL is needed.
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { resolveTokenMock } = vi.hoisted(() => ({ resolveTokenMock: vi.fn() }))

// The real token comes from the live URL; feed a fixed token so the resolver
// effect fires deterministically.
vi.mock("../shareTokenFromPathname", () => ({
  shareTokenFromLocation: () => "tok",
  shareTokenFromPathname: () => "tok",
}))
vi.mock("../resolveToken", () => ({ resolveToken: resolveTokenMock }))
vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("notFound() must not fire for a ready view")
  },
}))
// CommentsPanel fetches its list on mount — stub it out; it is not under test.
vi.mock("../../components/design-agent/CommentsPanel", () => ({
  CommentsPanel: () => null,
}))

import { PublicTokenViewer } from "../PublicTokenViewer"
import { DeviceBadge } from "../../components/design-agent/DeviceBadge"

function readyView(target_platform: string) {
  return {
    share_mode: "public" as const,
    requires_passcode: false,
    bundle_url: "https://cdn.example/p/abc/index.html",
    is_complete: false,
    company_slug: "acme",
    target_platform,
  }
}

async function renderReady(target_platform: string) {
  resolveTokenMock.mockResolvedValue(readyView(target_platform))
  const utils = render(<PublicTokenViewer />)
  await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
  return utils
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PublicTokenViewer — single-device toggle gate + device badge", () => {
  it("mobile-only: hides the Desktop/Mobile toggle and shows a Mobile badge in the mobile stage", async () => {
    const { container } = await renderReady("mobile")
    // The functional Desktop/Mobile toggle is gone (its aria-label is the
    // distinguishing marker — the Mark/Comment group reuses .platform-toggle).
    expect(container.querySelector('[aria-label="Preview platform"]')).toBeNull()
    // The static badge fills the slot.
    const badge = screen.getByLabelText("Mobile prototype")
    expect(badge.className).toContain("device-badge")
    expect(badge.textContent).toContain("Mobile")
    expect(badge.querySelector("svg")).not.toBeNull()
    // Stage default mirrors the prototype's form factor (mobile bezel).
    expect(screen.getByTestId("proto-stage").className).toContain("mobile")
  })

  it("desktop-only: hides the toggle and shows a Desktop badge in the desktop stage", async () => {
    const { container } = await renderReady("desktop")
    expect(container.querySelector('[aria-label="Preview platform"]')).toBeNull()
    const badge = screen.getByLabelText("Desktop prototype")
    expect(badge.className).toContain("device-badge")
    expect(badge.textContent).toContain("Desktop")
    expect(badge.querySelector("svg")).not.toBeNull()
    expect(screen.getByTestId("proto-stage").className).toContain("desktop")
  })

  it("both: renders the toggle as before and shows NO device badge (no regression)", async () => {
    const { container } = await renderReady("both")
    const toggle = container.querySelector('[aria-label="Preview platform"]')
    expect(toggle).not.toBeNull()
    expect(toggle!.textContent).toContain("Desktop")
    expect(toggle!.textContent).toContain("Mobile")
    expect(container.querySelector(".device-badge")).toBeNull()
  })

  it("legacy/unknown platform behaves like 'both' (toggle shown, no badge)", async () => {
    const { container } = await renderReady("web")
    expect(container.querySelector('[aria-label="Preview platform"]')).not.toBeNull()
    expect(container.querySelector(".device-badge")).toBeNull()
  })
})

describe("DeviceBadge leaf", () => {
  it("renders a non-interactive labelled pill with an inline SVG (not emoji) for mobile", () => {
    const { container } = render(<DeviceBadge platform="mobile" />)
    const badge = screen.getByLabelText("Mobile prototype")
    // Display-only: a <div>, not a button, and not in the tab order.
    expect(badge.tagName).toBe("DIV")
    expect(badge.getAttribute("tabindex")).toBeNull()
    expect(badge.getAttribute("role")).toBeNull()
    // Inline SVG icon, no emoji glyph.
    expect(container.querySelector("svg")).not.toBeNull()
    expect(badge.textContent).toBe("Mobile")
  })

  it("labels the desktop variant", () => {
    render(<DeviceBadge platform="desktop" />)
    expect(screen.getByLabelText("Desktop prototype").textContent).toBe("Desktop")
  })

  it("renders nothing for 'both' / legacy / unknown values", () => {
    for (const p of ["both", "web", ""]) {
      const { container } = render(<DeviceBadge platform={p} />)
      expect(container.firstChild).toBeNull()
      cleanup()
    }
  })
})
