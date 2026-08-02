/**
 * @vitest-environment jsdom
 *
 * Screenshot-as-context: the FOURTH design-source option in the GenerateModal.
 * Pill render/select, the sequential multi-slot strip (Option B — up to 10
 * screenshots, one upload at a time), disabled-gating, error handling, and
 * preference isolation — the DOM half of the screenshot-source flow (the
 * request-threading half is asserted at the buildGenerateParams level in
 * GenerateModal.design-source.test.tsx, per that file's node-env header).
 *
 * Reuses GenerateModalImageSteer.dom.test.tsx's rig: jsdom +
 * @testing-library/react, canvas downscale stubbed via the `_testDownscale`
 * prop (jsdom has no real canvas / image decode), NavigationContext +
 * DesignAgentDrawer.runGenerateFlow mocked. buildGenerateParams is the REAL
 * implementation (importOriginal), so the generate-click assertions exercise
 * the true body construction.
 */
import * as React from "react"
import { render, fireEvent, waitFor, act } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal } from "../GenerateModal"
import { runGenerateFlow } from "../DesignAgentDrawer"
import { designAgentApi, ApiError } from "../../../lib/api"

// ── Fixtures ────────────────────────────────────────────────────────────────

const PRD_ID = 77
// Deterministic downscale output with KNOWN bytes, so the uploaded Blob's
// decoded size/type can be asserted against the data URL's payload.
const STUB_BYTES = "stub-downscaled-bytes"
const STUB_DATA_URL = `data:image/png;base64,${btoa(STUB_BYTES)}`
const KEY_1 = "da-upload/ws1/first.png"
const KEY_2 = "da-upload/ws1/second.png"
const KEY_3 = "da-upload/ws1/third.png"

function screenshotProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    // Loaded-but-empty connector/repo state: no fetch effects fire, no
    // connector is active, and the config form renders (no saved preference).
    _testConnections: [],
    _testRepos: [],
    _testInitSource: "screenshot" as const,
    // Deterministic downscale — jsdom has no canvas.
    _testDownscale: vi.fn().mockResolvedValue(STUB_DATA_URL),
    ...overrides,
  }
}

function q(container: HTMLElement, testid: string) {
  return container.querySelector(`[data-testid="${testid}"]`)
}

function pill(container: HTMLElement, val: string) {
  return container.querySelector<HTMLButtonElement>(
    `.radio-pill[data-val="${val}"]`,
  )
}

function attachFile(container: HTMLElement, file: File) {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="screenshot-file-input"]',
  )
  expect(input).toBeTruthy()
  act(() => {
    fireEvent.change(input!, { target: { files: [file] } })
  })
}

function pngFile(name = "reference.png", type = "image/png") {
  return new File(["x"], name, { type })
}

function generateBtn(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>(
    '[data-testid="generate-btn"]',
  )!
}

function addBtn(container: HTMLElement) {
  return q(container, "screenshot-strip-add") as HTMLButtonElement | null
}

function removeBtn(container: HTMLElement, n: number) {
  return container.querySelector<HTMLButtonElement>(
    `[aria-label="Remove screenshot ${n}"]`,
  )
}

function tiles(container: HTMLElement) {
  return container.querySelectorAll('[aria-label^="Remove screenshot"]')
}

function uploadSpy() {
  return vi.spyOn(designAgentApi, "uploadScreenshot")
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

// ── AC1 — pill renders + selects ─────────────────────────────────────────────

describe("the Screenshot source pill", () => {
  it("test_screenshot_source_pill_renders_and_selects — renders beside the existing three, selectable with no connector; _testInitSource='screenshot' initializes it", async () => {
    // Start on the default (website) source: all four pills render.
    const { container } = render(
      React.createElement(
        GenerateModal,
        screenshotProps({ _testInitSource: "website" }),
      ),
    )
    for (const val of ["github", "figma", "website", "screenshot"]) {
      expect(pill(container, val)).toBeTruthy()
    }
    const shot = pill(container, "screenshot")!
    expect(shot.textContent).toBe("Screenshot")
    expect(shot.getAttribute("aria-pressed")).toBe("false")
    // No connector gate: clicking it selects it and reveals the picker.
    expect(q(container, "screenshot-file-input")).toBeNull()
    act(() => {
      shot.click()
    })
    await waitFor(() =>
      expect(pill(container, "screenshot")!.getAttribute("aria-pressed")).toBe(
        "true",
      ),
    )
    expect(q(container, "screenshot-file-input")).toBeTruthy()

    // The extended test seam initializes screenshot mode directly.
    const { container: c2 } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )
    expect(pill(c2, "screenshot")!.getAttribute("aria-pressed")).toBe("true")
    expect(q(c2, "screenshot-file-input")).toBeTruthy()
  })
})

// ── AC1 — appends, never replaces (regression) ───────────────────────────────

describe("multi-slot upload sequencing", () => {
  it("test_screenshot_source_second_pick_appends_not_replaces — two sequential successful picks leave 2 tiles, not 1", async () => {
    uploadSpy()
      .mockResolvedValueOnce({ screenshot_key: KEY_1, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_2, media_type: "image/png" })
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    attachFile(container, pngFile("first.png"))
    await waitFor(() => expect(tiles(container).length).toBe(1))

    attachFile(container, pngFile("second.png"))
    await waitFor(() => expect(tiles(container).length).toBe(2))

    // Both tiles present, correctly numbered — not a replace.
    expect(removeBtn(container, 1)).toBeTruthy()
    expect(removeBtn(container, 2)).toBeTruthy()
  })

  it("test_screenshot_source_failed_pick_does_not_consume_a_slot — an oversize file leaves the strip at its pre-pick count, error line shown", async () => {
    uploadSpy().mockResolvedValue({ screenshot_key: KEY_1, media_type: "image/png" })
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    attachFile(container, pngFile("first.png"))
    await waitFor(() => expect(tiles(container).length).toBe(1))

    const oversize = pngFile("too-big.png")
    Object.defineProperty(oversize, "size", { value: 6 * 1024 * 1024 })
    attachFile(container, oversize)

    await waitFor(() => expect(q(container, "screenshot-error")).toBeTruthy())
    expect(q(container, "screenshot-error")!.textContent).toBe(
      "That screenshot is over 5 MB — attach a smaller one.",
    )
    // Strip unchanged at its pre-pick count — the failed pick consumed no slot.
    expect(tiles(container).length).toBe(1)
  })

  it("test_screenshot_source_remove_reindexes_remaining_tiles — AC3; 3 attached, remove the 2nd, remaining 2 tiles are labelled 1 and 2 (not 1/3)", async () => {
    uploadSpy()
      .mockResolvedValueOnce({ screenshot_key: KEY_1, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_2, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_3, media_type: "image/png" })
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    attachFile(container, pngFile("a.png"))
    await waitFor(() => expect(tiles(container).length).toBe(1))
    attachFile(container, pngFile("b.png"))
    await waitFor(() => expect(tiles(container).length).toBe(2))
    attachFile(container, pngFile("c.png"))
    await waitFor(() => expect(tiles(container).length).toBe(3))

    act(() => {
      removeBtn(container, 2)!.click()
    })

    await waitFor(() => expect(tiles(container).length).toBe(2))
    expect(removeBtn(container, 1)).toBeTruthy()
    expect(removeBtn(container, 2)).toBeTruthy()
    expect(removeBtn(container, 3)).toBeNull()
  })

  it("test_screenshot_source_generate_disabled_until_one_screenshot_succeeds — AC7; Generate stays disabled at 0 attached, enables at 1", async () => {
    uploadSpy().mockResolvedValue({ screenshot_key: KEY_1, media_type: "image/png" })
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )
    expect(generateBtn(container).disabled).toBe(true)

    attachFile(container, pngFile())
    await waitFor(() => expect(generateBtn(container).disabled).toBe(false))
  })

  it("test_screenshot_source_ten_successful_picks_hides_add_button — AC4; 10 sequential successful picks hide the add control", async () => {
    const spy = uploadSpy()
    for (let i = 1; i <= 10; i++) {
      spy.mockResolvedValueOnce({
        screenshot_key: `da-upload/ws1/n${i}.png`,
        media_type: "image/png",
      })
    }
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    for (let i = 1; i <= 10; i++) {
      expect(addBtn(container)).toBeTruthy()
      attachFile(container, pngFile(`shot-${i}.png`))
      // eslint-disable-next-line no-loop-func
      await waitFor(() => expect(tiles(container).length).toBe(i))
    }
    expect(addBtn(container)).toBeNull()
  })

  it("test_screenshot_source_closing_modal_clears_strip — AC13; 2 attached, close, re-open → 0 attached", async () => {
    uploadSpy()
      .mockResolvedValueOnce({ screenshot_key: KEY_1, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_2, media_type: "image/png" })
    const props = screenshotProps()
    const { container, rerender } = render(React.createElement(GenerateModal, props))

    attachFile(container, pngFile("a.png"))
    await waitFor(() => expect(tiles(container).length).toBe(1))
    attachFile(container, pngFile("b.png"))
    await waitFor(() => expect(tiles(container).length).toBe(2))

    // Close (the modal's own close-handler state reset fires off `open`).
    rerender(React.createElement(GenerateModal, { ...props, open: false }))
    // Re-open.
    rerender(React.createElement(GenerateModal, { ...props, open: true }))

    await waitFor(() => expect(tiles(container).length).toBe(0))
  })

  it("test_screenshot_source_generate_click_sends_screenshot_keys_array_in_order — AC8; 3 successful picks in order A, B, C → the submitted body carries screenshot_keys: [A, B, C] in that exact order", async () => {
    uploadSpy()
      .mockResolvedValueOnce({ screenshot_key: KEY_1, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_2, media_type: "image/png" })
      .mockResolvedValueOnce({ screenshot_key: KEY_3, media_type: "image/png" })
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    attachFile(container, pngFile("a.png"))
    await waitFor(() => expect(tiles(container).length).toBe(1))
    attachFile(container, pngFile("b.png"))
    await waitFor(() => expect(tiles(container).length).toBe(2))
    attachFile(container, pngFile("c.png"))
    await waitFor(() => expect(tiles(container).length).toBe(3))

    await waitFor(() => expect(generateBtn(container).disabled).toBe(false))
    act(() => {
      generateBtn(container).click()
    })
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    const { params } = vi.mocked(runGenerateFlow).mock.calls[0][0]
    expect(params.design_source).toBe("screenshot")
    expect(params.screenshot_keys).toEqual([KEY_1, KEY_2, KEY_3])
    // Single-source cleanliness on the wire.
    expect(params.figma_file_key).toBeNull()
    expect(params.github_repo).toBeNull()
  })
})

// ── AC2 — upload flow + gating ───────────────────────────────────────────────

describe("upload flow", () => {
  it("test_upload_called_with_downscaled_blob_and_shown_as_a_tile — uploads the DOWNSCALED bytes (not the raw file) and renders it as a strip tile", async () => {
    const spy = uploadSpy().mockResolvedValue({
      screenshot_key: KEY_1,
      media_type: "image/png",
    })
    const props = screenshotProps()
    const { container } = render(React.createElement(GenerateModal, props))

    attachFile(container, pngFile())

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1))
    // The downscale seam ran on the picked file…
    expect(props._testDownscale).toHaveBeenCalledTimes(1)
    // …and the uploaded Blob is the DECODED downscaled payload, not the raw
    // 1-byte picked file: base64-decoded size + the data URL's media type.
    const sent = spy.mock.calls[0][0] as Blob
    expect(sent).toBeInstanceOf(Blob)
    expect(sent.type).toBe("image/png")
    expect(sent.size).toBe(STUB_BYTES.length)

    // Tile 1 shows the downscaled data URL.
    await waitFor(() => expect(tiles(container).length).toBe(1))
    const img = container.querySelector("img") as HTMLImageElement
    expect(img.getAttribute("src")).toBe(STUB_DATA_URL)
  })
})

// ── AC2 — error handling ─────────────────────────────────────────────────────

describe("server rejections + network failure", () => {
  it("test_413_and_422_surface_server_message_and_repick — both codes render the server message verbatim, generate stays disabled at 0, and a re-pick recovers", async () => {
    const cases = [
      { status: 413, detail: "File too large (max 8 MB)." },
      { status: 422, detail: "Unsupported file type (PNG, JPEG, or WebP required)." },
    ]
    for (const { status, detail } of cases) {
      const spy = uploadSpy().mockRejectedValueOnce(
        new ApiError(status, { detail }),
      )
      const { container, unmount } = render(
        React.createElement(GenerateModal, screenshotProps()),
      )

      attachFile(container, pngFile())
      await waitFor(() => expect(q(container, "screenshot-error")).toBeTruthy())
      // The server's user-readable message, verbatim.
      expect(q(container, "screenshot-error")!.textContent).toBe(detail)
      // Re-pickable state: no tile added, add control enabled, generate disabled.
      expect(tiles(container).length).toBe(0)
      expect((addBtn(container) as HTMLButtonElement).disabled).toBe(false)
      expect(generateBtn(container).disabled).toBe(true)

      // Re-pick succeeds → error clears, tile added, generate enabled.
      spy.mockResolvedValueOnce({
        screenshot_key: KEY_1,
        media_type: "image/png",
      })
      attachFile(container, pngFile("second-try.png"))
      await waitFor(() => expect(generateBtn(container).disabled).toBe(false))
      expect(q(container, "screenshot-error")).toBeNull()
      expect(tiles(container).length).toBe(1)

      unmount()
      vi.restoreAllMocks()
    }
  })

  it("test_network_failure_resets_picker — a fetch reject leaves the strip at 0, re-pickable, generate disabled", async () => {
    uploadSpy().mockRejectedValueOnce(new TypeError("Failed to fetch"))
    const { container } = render(
      React.createElement(GenerateModal, screenshotProps()),
    )

    attachFile(container, pngFile())
    await waitFor(() => expect(q(container, "screenshot-error")).toBeTruthy())
    // Pre-upload state: no tile added (generate disabled), add control enabled.
    expect(tiles(container).length).toBe(0)
    expect(generateBtn(container).disabled).toBe(true)
    expect((addBtn(container) as HTMLButtonElement).disabled).toBe(false)
  })
})

// ── AC5 — preference isolation ───────────────────────────────────────────────

describe("preference isolation", () => {
  it("test_screenshot_never_saved_as_preference — generating in screenshot mode never writes a DesignSourcePreference", async () => {
    uploadSpy().mockResolvedValue({
      screenshot_key: KEY_1,
      media_type: "image/png",
    })
    const onSavePreference = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(
        GenerateModal,
        screenshotProps({ onSavePreference }),
      ),
    )

    attachFile(container, pngFile())
    await waitFor(() => expect(generateBtn(container).disabled).toBe(false))
    act(() => {
      generateBtn(container).click()
    })
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(onSavePreference).not.toHaveBeenCalled()
  })

  it("the spy wiring is live: a website-mode generate DOES save the preference (control case)", async () => {
    const onSavePreference = vi.fn().mockResolvedValue(undefined)
    const { container } = render(
      React.createElement(
        GenerateModal,
        screenshotProps({ onSavePreference, _testInitSource: "website" }),
      ),
    )
    act(() => {
      generateBtn(container).click()
    })
    await waitFor(() => expect(onSavePreference).toHaveBeenCalledTimes(1))
    expect(onSavePreference).toHaveBeenCalledWith(
      expect.objectContaining({ design_source: "website" }),
    )
  })
})
