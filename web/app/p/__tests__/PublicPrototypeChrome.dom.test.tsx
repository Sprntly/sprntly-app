// @vitest-environment jsdom
//
// Direct, props-driven DOM tests for PublicPrototypeChrome — the anon-viewer
// chrome extracted from PublicTokenViewer.tsx. Unlike PublicTokenViewer.dom.
// test.tsx (which must mock the token-resolution effect + the live URL to
// reach the ready state), this component takes its ready-state data as props,
// so it mounts directly with no resolveToken / share-token / router mocking.
// CommentsPanel is stubbed (it fetches its own list on mount and is not under
// test here) and designAgentApi's by-token methods are mocked so no real
// network call fires — the same posture as the sibling PublicTokenViewer.dom
// test, just without the resolver layer above it.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { listCommentsByTokenMock, createCommentByTokenMock } = vi.hoisted(() => ({
  listCommentsByTokenMock: vi.fn(),
  createCommentByTokenMock: vi.fn(),
}))

// CommentsPanel (the Pinned-section body) fetches its own list on mount and is
// not under test here — stub the CONTAINER only. importActual keeps
// CommentAvatar/shortRelativeTime real: the General section imports them from
// this same module for its own card rendering, and a bare factory would
// silently undefine them.
vi.mock("../../components/design-agent/CommentsPanel", async () => {
  const actual = await vi.importActual<typeof import("../../components/design-agent/CommentsPanel")>(
    "../../components/design-agent/CommentsPanel",
  )
  return { ...actual, CommentsPanel: () => null }
})
// designAgentApi.listCommentsByToken/createCommentByToken back the General
// section's own fetch/post (independent of CommentsPanel's internal fetch,
// which is stubbed away above) AND the pin-create path. importActual keeps
// every other export (types, other methods) real.
vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api")
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      listCommentsByToken: listCommentsByTokenMock,
      createCommentByToken: createCommentByTokenMock,
    },
  }
})

import { PublicPrototypeChrome } from "../PublicPrototypeChrome"

// Safe default so every test that doesn't care about comments still renders
// cleanly — the General section's own fetch fires unconditionally on mount.
listCommentsByTokenMock.mockResolvedValue([])
createCommentByTokenMock.mockResolvedValue({
  id: 999, anchor_id: null, body: "", author: "Anonymous",
  status: "open", created_at: "2026-01-01T00:00:00Z", resolved_at: null,
})

const BUNDLE_URL = "https://cdn.example/p/abc/index.html"

function renderChrome(overrides: Partial<React.ComponentProps<typeof PublicPrototypeChrome>> = {}) {
  return render(
    <PublicPrototypeChrome
      token="tok"
      bundleUrl={BUNDLE_URL}
      isComplete={false}
      targetPlatform="both"
      {...overrides}
    />,
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  listCommentsByTokenMock.mockResolvedValue([])
  // The name-capture flow persists the viewer's name to localStorage; without
  // clearing it a submitting test leaks a stored name into the next test, so the
  // name form never re-renders (a returning viewer is not re-prompted). jsdom
  // reuses one window across a file's tests, so isolate explicitly here.
  try {
    window.localStorage.clear()
  } catch {
    /* storage disabled (private mode) — nothing to clear */
  }
})

describe("PublicPrototypeChrome — creation", () => {
  it("test_chrome_renders_iframe_with_bundle_url: mounts an iframe pointed at bundleUrl", async () => {
    renderChrome()
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    const iframe = document.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute("src")).toBe(BUNDLE_URL)
  })

  it("test_chrome_renders_mark_and_comments_toggles: both head toggles are present", async () => {
    renderChrome()
    await waitFor(() => expect(screen.getByTestId("public-mark-toggle")).toBeTruthy())
    expect(screen.getByTestId("public-comments-toggle")).toBeTruthy()
  })

  it("test_chrome_both_platform_shows_toggle_no_badge: 'both' shows the Desktop/Mobile toggle, no DeviceBadge", async () => {
    const { container } = renderChrome({ targetPlatform: "both" })
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    const toggle = container.querySelector('[aria-label="Preview platform"]')
    expect(toggle).not.toBeNull()
    expect(container.querySelector(".device-badge")).toBeNull()
  })

  it("test_chrome_mobile_only_hides_toggle_shows_badge: 'mobile' hides the toggle, shows a Mobile badge, mobile stage default", async () => {
    const { container } = renderChrome({ targetPlatform: "mobile" })
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    expect(container.querySelector('[aria-label="Preview platform"]')).toBeNull()
    const badge = screen.getByLabelText("Mobile prototype")
    expect(badge.className).toContain("device-badge")
    expect(screen.getByTestId("proto-stage").className).toContain("mobile")
  })
})

describe("PublicPrototypeChrome — edge cases", () => {
  it("test_chrome_legacy_platform_value_behaves_like_both: an unrecognised platform string behaves like 'both'", async () => {
    const { container } = renderChrome({ targetPlatform: "web" })
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    expect(container.querySelector('[aria-label="Preview platform"]')).not.toBeNull()
    expect(container.querySelector(".device-badge")).toBeNull()
  })

  it("test_chrome_general_comments_empty_state: with no comments loaded, General renders empty-state copy + trigger, not a list", async () => {
    listCommentsByTokenMock.mockResolvedValue([])
    renderChrome()
    fireEvent.click(screen.getByTestId("public-comments-toggle"))
    // No stored viewer name yet — the name-capture form gates the sections. The
    // General/Pinned sections themselves are exercised in the container-level
    // dom test (PublicTokenViewer.dom.test.tsx); here we only need to prove the
    // props-driven mount reaches the empty state once past the name gate isn't
    // in the way, i.e. before any name is captured the form shows first.
    await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
      target: { value: "Ada Lovelace" },
    })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    await waitFor(() => expect(screen.getByTestId("general-comments-empty")).toBeTruthy())
    expect(screen.queryByTestId("general-comments-list")).toBeNull()
    expect(screen.getByTestId("general-comment-trigger")).toBeTruthy()
  })
})

describe("PublicPrototypeChrome — load mask", () => {
  const placeholderIn = (container: HTMLElement) =>
    container.querySelector('[data-testid="da-viewer-placeholder"]')

  it("test_public_chrome_masks_iframe_until_load: the neutral cover renders from first paint and lifts on the iframe load", async () => {
    const { container } = renderChrome()
    // Present synchronously on FIRST render — the anon viewer must never see the
    // unmasked white/black pre-paint while the bundle loads.
    expect(placeholderIn(container)).not.toBeNull()
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    // Still covered after mount effects settle (only the load event lifts it)…
    expect(placeholderIn(container)).not.toBeNull()
    const iframe = container.querySelector("iframe.da-prototype-iframe")
    expect(iframe).not.toBeNull()
    fireEvent.load(iframe!)
    // …and gone once the bundle painted.
    expect(placeholderIn(container)).toBeNull()
  })

  it("test_public_chrome_remasks_on_bundle_url_change: a new bundleUrl remounts the viewer and re-shows the cover", async () => {
    const NEW_BUNDLE_URL = "https://cdn.example/p/rotated/index.html"
    const { container, rerender } = renderChrome()
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    fireEvent.load(container.querySelector("iframe.da-prototype-iframe")!)
    expect(placeholderIn(container)).toBeNull()
    // Token re-resolution / signed-URL rotation hands the chrome a fresh
    // bundleUrl — the viewer must remount and re-mask until the new bundle paints.
    rerender(
      <PublicPrototypeChrome
        token="tok"
        bundleUrl={NEW_BUNDLE_URL}
        isComplete={false}
        targetPlatform="both"
      />,
    )
    expect(placeholderIn(container)).not.toBeNull()
    expect(container.querySelector("iframe")!.getAttribute("src")).toBe(NEW_BUNDLE_URL)
  })
})

describe("PublicPrototypeChrome — regression (extraction moved logic, did not duplicate it)", () => {
  it("test_chrome_pin_create_routes_via_token_not_authed: usePinMarking's injected onCreate calls createCommentByToken(token), end-to-end through a real pin drop + submit", async () => {
    renderChrome({ token: "regress-tok" })
    await waitFor(() => expect(screen.getByTestId("public-mark-toggle")).toBeTruthy())
    // Capture the name first (a pin submit aborts + re-surfaces the form
    // otherwise — requireName gate).
    fireEvent.click(screen.getByTestId("public-comments-toggle"))
    await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
      target: { value: "Ada Lovelace" },
    })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    // Name submit auto-enables mark mode (pin.setMarkMode(true)).
    await waitFor(() => expect(screen.getByTestId("mark-mode-notice")).toBeTruthy())
    // Drop a pin: click the (now-active) MarkOverlay. jsdom has no real layout,
    // so the iframe hit-test inside MarkOverlay/getElementAtIframePoint resolves
    // to a null anchor (elementFromPoint is unimplemented in jsdom) — the pin
    // still drops with anchor: null, exactly like a real cross-origin bundle
    // click where the anchor can't be resolved.
    fireEvent.click(screen.getByTestId("da-mark-overlay"))
    await waitFor(() => expect(screen.getByTestId("da-pin-input-1")).toBeTruthy())
    fireEvent.change(screen.getByTestId("da-pin-input-1"), {
      target: { value: "Move this up" },
    })
    fireEvent.click(screen.getByTestId("da-pin-submit-1"))
    await waitFor(() => expect(createCommentByTokenMock).toHaveBeenCalled())
    // The by-token route, with the share token — never the authed
    // createComment(prototype.id) signature.
    expect(createCommentByTokenMock).toHaveBeenCalledWith(
      "regress-tok",
      expect.objectContaining({
        anchor_id: "pin-1",
        body: "Move this up",
        viewer_name: "Ada Lovelace",
      }),
    )
  })
})
