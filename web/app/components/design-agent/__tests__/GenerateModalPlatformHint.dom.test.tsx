/**
 * @vitest-environment jsdom
 *
 * PRD-declared platform hint → GenerateModal platform DEFAULT.
 *
 * The parsed :::design block's platform_hint seeds the platform selector's
 * initial value only: the user's explicit toggle always wins, an absent hint
 * keeps DEFAULT_PLATFORM byte-identical behaviour, and the saved-preference
 * auto-skip path (a generation fired with ZERO user interaction) inherits the
 * hint by design — the PRD knows its surface.
 *
 * Reuses GenerateModalScreenshotSource.dom.test.tsx's rig: jsdom +
 * @testing-library/react, NavigationContext mocked, DesignAgentDrawer's
 * runGenerateFlow stubbed while buildGenerateParams stays the REAL
 * implementation (importOriginal) so the generate-click assertions exercise
 * the true body construction.
 */
import * as React from "react"
import { render, waitFor, act } from "@testing-library/react"
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

const PRD_ID = 91

function hintProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    // Loaded-but-empty connector/repo state: no fetch effects fire, no
    // connector is active, and the config form renders (no saved preference).
    _testConnections: [],
    _testRepos: [],
    _testInitSource: "website" as const,
    ...overrides,
  }
}

function platformPill(container: HTMLElement, val: string) {
  return container.querySelector<HTMLButtonElement>(
    `.radio-pill[data-val="${val}"]`,
  )!
}

function generateBtn(container: HTMLElement) {
  return container.querySelector<HTMLButtonElement>(
    '[data-testid="generate-btn"]',
  )!
}

function submittedParams() {
  const { params } = vi.mocked(runGenerateFlow).mock.calls[0][0]
  return params
}

beforeEach(() => {
  vi.mocked(runGenerateFlow).mockResolvedValue(undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

// ── default from hint ────────────────────────────────────────────────────────

describe("platform default from the PRD hint", () => {
  it("test_modal_defaults_platform_from_hint — mobile/desktop/both each open pre-selected; absent hint keeps the existing default", () => {
    const cases: Array<["desktop" | "mobile" | "both", string]> = [
      ["mobile", "mobile"],
      ["desktop", "desktop"],
      ["both", "both"],
    ]
    for (const [hint, expected] of cases) {
      const { container, unmount } = render(
        React.createElement(GenerateModal, hintProps({ platformHint: hint })),
      )
      expect(platformPill(container, expected).getAttribute("aria-pressed")).toBe("true")
      for (const other of ["desktop", "mobile", "both"].filter((v) => v !== expected)) {
        expect(platformPill(container, other).getAttribute("aria-pressed")).toBe("false")
      }
      unmount()
    }

    // Absent hint → today's DEFAULT_PLATFORM ("both"), byte-identical behaviour.
    const { container } = render(React.createElement(GenerateModal, hintProps()))
    expect(platformPill(container, "both").getAttribute("aria-pressed")).toBe("true")

    // Explicit null (the wired-props shape when no hint exists) — same default.
    const { container: c2 } = render(
      React.createElement(GenerateModal, hintProps({ platformHint: null })),
    )
    expect(platformPill(c2, "both").getAttribute("aria-pressed")).toBe("true")
  })
})

// ── PM override wins ─────────────────────────────────────────────────────────

describe("explicit toggle overrides the hint", () => {
  it("test_toggle_overrides_hint_in_generate_body — hint=mobile, user toggles Desktop, generate body carries desktop", async () => {
    const { container } = render(
      React.createElement(GenerateModal, hintProps({ platformHint: "mobile" })),
    )
    expect(platformPill(container, "mobile").getAttribute("aria-pressed")).toBe("true")

    act(() => {
      platformPill(container, "desktop").click()
    })
    expect(platformPill(container, "desktop").getAttribute("aria-pressed")).toBe("true")

    act(() => {
      generateBtn(container).click()
    })
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    // REAL buildGenerateParams ran — the toggled value wins, not the hint.
    expect(submittedParams().target_platform).toBe("desktop")
  })

  it("test_hint_prop_optional_no_regression — no hint prop: default platform travels in the generate body exactly as before", async () => {
    const { container } = render(React.createElement(GenerateModal, hintProps()))
    expect(platformPill(container, "both").getAttribute("aria-pressed")).toBe("true")

    act(() => {
      generateBtn(container).click()
    })
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(submittedParams().target_platform).toBe("both")
  })
})

// ── auto-skip inherits the hint (intended) ───────────────────────────────────

describe("saved-preference auto-skip", () => {
  it("test_auto_skip_generation_carries_hinted_platform — healthy website preference + hint=mobile fires a mobile generation with zero interaction", async () => {
    const onClose = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        hintProps({
          platformHint: "mobile",
          onClose,
          savedPreference: { design_source: "website" },
        }),
      ),
    )

    // No clicks at all: the auto-skip effect fires the generation itself.
    await waitFor(() =>
      expect(vi.mocked(runGenerateFlow)).toHaveBeenCalledTimes(1),
    )
    expect(submittedParams().target_platform).toBe("mobile")
    expect(submittedParams().design_source).toBe("website")
    // The auto-skip path closed the modal before firing.
    expect(onClose).toHaveBeenCalled()
  })
})
