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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const { resolveTokenMock, listCommentsByTokenMock, createCommentByTokenMock } = vi.hoisted(() => ({
  resolveTokenMock: vi.fn(),
  listCommentsByTokenMock: vi.fn(),
  createCommentByTokenMock: vi.fn(),
}))

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
// CommentsPanel (the Pinned-section body) fetches its own list on mount and is
// not under test here — stub the CONTAINER only. importActual keeps
// CommentAvatar/shortRelativeTime real: PublicTokenViewer's new General
// section imports them from this same module for its own card rendering, and
// a bare `() => ({ CommentsPanel: ... })` factory would silently undefine them.
vi.mock("../../components/design-agent/CommentsPanel", async () => {
  const actual = await vi.importActual<typeof import("../../components/design-agent/CommentsPanel")>(
    "../../components/design-agent/CommentsPanel",
  )
  return { ...actual, CommentsPanel: () => null }
})
// designAgentApi.listCommentsByToken/createCommentByToken back the new General
// section's own fetch/post (independent of CommentsPanel's internal fetch,
// which is stubbed away above). importActual keeps every other export (types,
// other methods) real.
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

import { PublicTokenViewer } from "../PublicTokenViewer"
import { DeviceBadge } from "../../components/design-agent/DeviceBadge"

// Safe default so every test that doesn't care about comments (single-device,
// name-capture) still renders cleanly — the General section's own fetch fires
// unconditionally on mount. `clearAllMocks()` in afterEach clears call history
// only, not this implementation, so it holds across the whole file unless a
// test overrides it with mockResolvedValueOnce/mockResolvedValue.
listCommentsByTokenMock.mockResolvedValue([])
createCommentByTokenMock.mockResolvedValue({
  id: 999, anchor_id: null, body: "", author: "Anonymous",
  status: "open", created_at: "2026-01-01T00:00:00Z", resolved_at: null,
})

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

describe("PublicTokenViewer — single full-name field + mark-mode auto-enable", () => {
  async function openNameForm() {
    const utils = await renderReady("both")
    // Open the comments sidebar → with no stored name, the capture form appears.
    fireEvent.click(screen.getByTestId("public-comments-toggle"))
    await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
    return utils
  }

  it("test_public_name_form_single_full_name_field: exactly one 'Full name' input, no first/last", async () => {
    const { container } = await openNameForm()
    const input = screen.getByTestId("viewer-full-name-input")
    expect(input.getAttribute("placeholder")).toBe("Full name")
    expect(container.querySelector('[data-testid="viewer-first-name-input"]')).toBeNull()
    expect(container.querySelector('[data-testid="viewer-last-name-input"]')).toBeNull()
    // Single-line control, not a tall multi-line box: a real <input type="text">,
    // and it lives inside the row-flex `.da-viewer-name-fields` wrapper so its
    // `flex: 1 1 120px` grows horizontally (full width) instead of stretching
    // vertically as a direct child of the column form.
    expect(input.tagName).toBe("INPUT")
    expect(input.getAttribute("type")).toBe("text")
    expect(input.closest(".da-viewer-name-fields")).not.toBeNull()
  })

  it("test_submit_disabled_when_fullname_empty: disabled on empty/whitespace, enabled with content", async () => {
    await openNameForm()
    const submit = screen.getByTestId("viewer-name-submit") as HTMLButtonElement
    const input = screen.getByTestId("viewer-full-name-input")
    expect(submit.disabled).toBe(true)
    fireEvent.change(input, { target: { value: "   " } })
    expect(submit.disabled).toBe(true)
    fireEvent.change(input, { target: { value: "Ada Lovelace" } })
    expect(submit.disabled).toBe(false)
  })

  it("test_name_submit_sets_viewer_name_and_auto_enables_mark_mode", async () => {
    const { container } = await openNameForm()
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
      target: { value: "Ada Lovelace" },
    })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    // Name gate clears → identity strip shows the full name, no undefined artifact.
    await waitFor(() => expect(screen.getByTestId("viewer-identity-strip")).toBeTruthy())
    const strip = screen.getByTestId("viewer-identity-strip")
    expect(strip.textContent).toContain("Ada Lovelace")
    expect(strip.textContent).not.toContain("undefined")
    // The capture form is gone.
    expect(container.querySelector('[data-testid="viewer-name-form"]')).toBeNull()
    // Mark mode auto-enabled (setMarkMode(true), not toggle): the Mark button is
    // pressed + distinctly styled, the canvas is in marking mode, the notice shows.
    const markBtn = screen.getByTestId("public-mark-toggle")
    expect(markBtn.getAttribute("aria-pressed")).toBe("true")
    expect(markBtn.className).toContain("mark-active")
    expect(screen.getByTestId("da-canvas-center").className).toContain("marking")
    expect(screen.getByTestId("mark-mode-notice")).toBeTruthy()
  })

  it("test_avatar_initials_from_full_name: up-to-two initials, no empty segment", async () => {
    await openNameForm()
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), {
      target: { value: "Ada Lovelace" },
    })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    await waitFor(() => expect(screen.getByTestId("viewer-identity-strip")).toBeTruthy())
    const av = screen.getByTestId("viewer-identity-strip").querySelector(".pc-av")
    expect(av?.textContent).toBe("AL")
  })
})

describe("PublicTokenViewer — General / Pinned comment sections", () => {
  // Drive the REAL name-capture flow (not a raw localStorage.setItem — this
  // repo's jsdom env throws on localStorage writes; the app's own persist call
  // guards with try/catch, but bypassing the flow from the test would not) to
  // clear the name gate, then open the sidebar onto the General/Pinned sections.
  async function openSectionsWithName() {
    const utils = await renderReady("both")
    fireEvent.click(screen.getByTestId("public-comments-toggle"))
    await waitFor(() => expect(screen.getByTestId("viewer-name-form")).toBeTruthy())
    fireEvent.change(screen.getByTestId("viewer-full-name-input"), { target: { value: "Ada Lovelace" } })
    fireEvent.submit(screen.getByTestId("viewer-name-form"))
    await waitFor(() => expect(screen.getByTestId("general-comments-section")).toBeTruthy())
    return utils
  }

  const GENERAL = {
    id: 1, anchor_id: null, body: "Overall the flow feels smooth", author: "Sarah Chen",
    status: "open" as const, created_at: "2026-01-01T00:00:05Z", resolved_at: null,
    pin_x_pct: null, pin_y_pct: null,
  }
  const PINNED = {
    id: 2, anchor_id: "deadbeef", body: "This button needs more weight", author: "Jane Doe",
    status: "open" as const, created_at: "2026-01-01T00:00:01Z", resolved_at: null,
    pin_x_pct: 10, pin_y_pct: 20,
  }

  it("renders both sections with headers + open counts when mixed general/pinned data exists", async () => {
    listCommentsByTokenMock.mockResolvedValue([PINNED, GENERAL])
    await openSectionsWithName()
    expect(screen.getByTestId("pinned-comments-section")).toBeTruthy()
    expect(screen.getByTestId("general-comments-section").textContent).toContain("General")
    expect(screen.getByTestId("pinned-comments-section").textContent).toContain("Pinned")

    await waitFor(() => expect(screen.getByTestId(`general-comment-thread-${GENERAL.id}`)).toBeTruthy())
    // split by pin_x_pct (+ anchor_id, see the dedicated test below): the null-pin
    // row renders in General, the positioned row does NOT appear there.
    expect(screen.queryByTestId(`general-comment-thread-${PINNED.id}`)).toBeNull()
    const generalSection = screen.getByTestId("general-comments-section")
    expect(generalSection.querySelector(".comments-section-count")?.textContent).toBe("1")
    const pinnedSection = screen.getByTestId("pinned-comments-section")
    expect(pinnedSection.querySelector(".comments-section-count")?.textContent).toBe("1")
  })

  it("general card uses .comment-thread--general and never carries a .proto-comment-pin badge", async () => {
    listCommentsByTokenMock.mockResolvedValue([GENERAL])
    await openSectionsWithName()
    await waitFor(() => expect(screen.getByTestId(`general-comment-thread-${GENERAL.id}`)).toBeTruthy())
    const card = screen.getByTestId(`general-comment-thread-${GENERAL.id}`)
    expect(card.className).toContain("comment-thread")
    expect(card.className).toContain("comment-thread--general")
    expect(card.querySelector(".proto-comment-pin")).toBeNull()
    expect(card.textContent).toContain("Sarah Chen")
    expect(card.textContent).toContain("Overall the flow feels smooth")
  })

  it("an anchor-only comment (no pin position, e.g. right-click composer) is NOT miscategorized as General", async () => {
    // Data-model nuance: general = null pin_x_pct AND null anchor_id. A comment
    // with an anchor but no x/y position (the existing right-click-anywhere
    // composer path) must stay out of General.
    const anchorOnlyNoPin = {
      id: 3, anchor_id: "rightclick-anchor", body: "Right-click anchored feedback",
      author: "Tom R.", status: "open" as const, created_at: "2026-01-01T00:00:02Z",
      resolved_at: null, pin_x_pct: null, pin_y_pct: null,
    }
    listCommentsByTokenMock.mockResolvedValue([anchorOnlyNoPin])
    await openSectionsWithName()
    await waitFor(() => expect(screen.getByTestId("general-comments-empty")).toBeTruthy())
    expect(screen.queryByTestId(`general-comment-thread-${anchorOnlyNoPin.id}`)).toBeNull()
    expect(screen.getByTestId("pinned-comments-section").querySelector(".comments-section-count")?.textContent).toBe("1")
  })

  it("empty state: both section headers still render; General shows the trigger + empty copy", async () => {
    listCommentsByTokenMock.mockResolvedValue([])
    await openSectionsWithName()
    expect(screen.getByTestId("pinned-comments-section")).toBeTruthy()
    expect(screen.getByTestId("general-comment-trigger")).toBeTruthy()
    expect(screen.getByTestId("general-comments-empty")).toBeTruthy()
    // No count badge when nothing is open in a section.
    expect(screen.getByTestId("general-comments-section").querySelector(".comments-section-count")).toBeNull()
  })

  it("trigger opens an inline composer; Send posts a null-anchor comment and prepends it to General", async () => {
    listCommentsByTokenMock.mockResolvedValue([])
    const created = {
      id: 42, anchor_id: null, body: "Nice palette overall", author: "Ada Lovelace",
      status: "open" as const, created_at: "2026-01-01T00:10:00Z", resolved_at: null,
      pin_x_pct: null, pin_y_pct: null,
    }
    createCommentByTokenMock.mockResolvedValue(created)
    await openSectionsWithName()

    fireEvent.click(screen.getByTestId("general-comment-trigger"))
    expect(screen.queryByTestId("general-comment-trigger")).toBeNull() // trigger unmounts while composing
    const textarea = screen.getByTestId("general-comment-input")
    fireEvent.change(textarea, { target: { value: "Nice palette overall" } })
    fireEvent.click(screen.getByTestId("general-comment-send"))

    await waitFor(() => expect(createCommentByTokenMock).toHaveBeenCalled())
    expect(createCommentByTokenMock).toHaveBeenCalledWith("tok", {
      body: "Nice palette overall",
      anchor_id: null,
      pin_x_pct: null,
      pin_y_pct: null,
      viewer_name: "Ada Lovelace",
    })
    // Prepended, composer closed, trigger reappears.
    await waitFor(() => expect(screen.getByTestId(`general-comment-thread-${created.id}`)).toBeTruthy())
    expect(screen.queryByTestId("general-comment-input")).toBeNull()
    expect(screen.getByTestId("general-comment-trigger")).toBeTruthy()
  })

  it("Cancel closes the composer without posting", async () => {
    listCommentsByTokenMock.mockResolvedValue([])
    await openSectionsWithName()
    fireEvent.click(screen.getByTestId("general-comment-trigger"))
    fireEvent.change(screen.getByTestId("general-comment-input"), { target: { value: "abandoned draft" } })
    fireEvent.click(screen.getByTestId("general-comment-cancel"))
    expect(createCommentByTokenMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId("general-comment-input")).toBeNull()
    expect(screen.getByTestId("general-comment-trigger")).toBeTruthy()
  })

  it("Send is disabled when the textarea is empty or whitespace-only", async () => {
    listCommentsByTokenMock.mockResolvedValue([])
    await openSectionsWithName()
    fireEvent.click(screen.getByTestId("general-comment-trigger"))
    const send = screen.getByTestId("general-comment-send") as HTMLButtonElement
    expect(send.disabled).toBe(true)
    fireEvent.change(screen.getByTestId("general-comment-input"), { target: { value: "   " } })
    expect(send.disabled).toBe(true)
    fireEvent.change(screen.getByTestId("general-comment-input"), { target: { value: "Real feedback" } })
    expect(send.disabled).toBe(false)
  })
})
