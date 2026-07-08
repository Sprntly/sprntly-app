// @vitest-environment jsdom
//
// End-to-end DOM test for the passcode-protected viewer gaining the same
// mark-tool/comments chrome the public-link viewer already has. Unlike
// page.test.tsx (node-env, renderToStaticMarkup only), this mounts the REAL
// stateful PasscodeGate, drives a passcode submit through a mocked global
// fetch, and asserts the real PublicPrototypeChrome renders post-verification
// — proving the container → view → chrome wiring end-to-end, not just the
// presentational branch in isolation.
//
// CommentsPanel is stubbed (it fetches its own list on mount and is not under
// test here) and designAgentApi's by-token methods are mocked so no real
// network call fires for the General section / pin-create path — the same
// posture as the sibling PublicPrototypeChrome.dom.test.tsx.
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

import { PasscodeGate } from "../PasscodeGate"

// Safe default so every test that doesn't care about comments still renders
// cleanly — the General section's own fetch fires unconditionally on mount.
listCommentsByTokenMock.mockResolvedValue([])
createCommentByTokenMock.mockResolvedValue({
  id: 999, anchor_id: null, body: "", author: "Anonymous",
  status: "open", created_at: "2026-01-01T00:00:00Z", resolved_at: null,
})

const BUNDLE_URL = "https://cdn.example/p/xyz/index.html"

/** Mock the global fetch the passcode POST uses (PasscodeGate/submitPasscode
 * calls the ambient `fetch`, not an injected fetchImpl, so the container-level
 * flow must stub the global — matching page.test.tsx's mockFetch helper). */
function mockPasscodeFetch(res: { status: number; ok?: boolean; body?: unknown }) {
  const fn = vi.fn().mockResolvedValue({
    status: res.status,
    ok: res.ok ?? (res.status >= 200 && res.status < 300),
    json: async () => res.body ?? {},
  })
  vi.stubGlobal("fetch", fn)
  return fn
}

async function submitPasscodeForm(passcode = "hunter2") {
  fireEvent.change(screen.getByLabelText(/enter passcode to view prototype/i), {
    target: { value: passcode },
  })
  fireEvent.click(screen.getByRole("button", { name: /continue/i }))
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  listCommentsByTokenMock.mockResolvedValue([])
  // The name-capture flow (inside the mounted chrome) persists the viewer's
  // name to localStorage; without clearing it a submitting test leaks a
  // stored name into the next test, so the name form never re-renders (a
  // returning viewer is not re-prompted). jsdom reuses one window across a
  // file's tests, so isolate explicitly here — same fix as
  // PublicTokenViewer.dom.test.tsx / PublicPrototypeChrome.dom.test.tsx.
  try {
    window.localStorage.clear()
  } catch {
    /* storage disabled (private mode) — nothing to clear */
  }
})

describe("PasscodeGate — chrome parity (creation)", () => {
  it("test_passcode_verified_view_renders_mark_and_comments_toggles: a correct passcode reaches the real chrome's mark + comments toggles", async () => {
    mockPasscodeFetch({
      status: 200,
      body: { bundle_url: BUNDLE_URL, is_complete: false, target_platform: "both" },
    })
    render(<PasscodeGate token="tok" />)
    await submitPasscodeForm()
    await waitFor(() => expect(screen.getByTestId("public-mark-toggle")).toBeTruthy())
    expect(screen.getByTestId("public-comments-toggle")).toBeTruthy()
  })

  it("test_passcode_verified_view_renders_comment_sections: general + pinned comment sections render post-verify", async () => {
    mockPasscodeFetch({
      status: 200,
      body: { bundle_url: BUNDLE_URL, is_complete: false, target_platform: "both" },
    })
    render(<PasscodeGate token="tok" />)
    await submitPasscodeForm()
    await waitFor(() => expect(screen.getByTestId("public-comments-toggle")).toBeTruthy())
    fireEvent.click(screen.getByTestId("public-comments-toggle"))
    await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
      target: { value: "Ada Lovelace" },
    })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    await waitFor(() => expect(screen.getByTestId("general-comments-section")).toBeTruthy())
    expect(screen.getByTestId("pinned-comments-section")).toBeTruthy()
  })
})

describe("PasscodeGate — chrome parity (edge cases: target_platform threading)", () => {
  it("test_passcode_mobile_only_hides_toggle_shows_badge: target_platform 'mobile' hides the toggle and shows a Mobile badge", async () => {
    mockPasscodeFetch({
      status: 200,
      body: { bundle_url: BUNDLE_URL, is_complete: false, target_platform: "mobile" },
    })
    const { container } = render(<PasscodeGate token="tok" />)
    await submitPasscodeForm()
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    expect(container.querySelector('[aria-label="Preview platform"]')).toBeNull()
    const badge = screen.getByLabelText("Mobile prototype")
    expect(badge.className).toContain("device-badge")
  })

  it("test_passcode_missing_target_platform_defaults_both: a response omitting target_platform (legacy row) degrades to 'both'", async () => {
    mockPasscodeFetch({
      status: 200,
      // Legacy/omitted response body — no target_platform key at all.
      body: { bundle_url: BUNDLE_URL, is_complete: false },
    })
    const { container } = render(<PasscodeGate token="tok" />)
    await submitPasscodeForm()
    await waitFor(() => expect(screen.getByTestId("da-ready")).toBeTruthy())
    expect(container.querySelector('[aria-label="Preview platform"]')).not.toBeNull()
    expect(container.querySelector(".device-badge")).toBeNull()
  })
})

describe("PasscodeGate — chrome parity (error handling, unaffected by the widening)", () => {
  it("test_submit_passcode_still_maps_401_429_correctly: 401 and 429 still surface their distinct messages through the real submit flow, never reaching the chrome", async () => {
    mockPasscodeFetch({ status: 401, ok: false })
    render(<PasscodeGate token="tok" />)
    await submitPasscodeForm("wrong")
    await waitFor(() =>
      expect(screen.getByText(/incorrect passcode/i)).toBeTruthy(),
    )
    expect(screen.queryByTestId("da-ready")).toBeNull()

    mockPasscodeFetch({ status: 429, ok: false })
    await submitPasscodeForm("wrong-again")
    await waitFor(() =>
      expect(screen.getByText(/too many attempts/i)).toBeTruthy(),
    )
    expect(screen.queryByTestId("da-ready")).toBeNull()
  })
})
