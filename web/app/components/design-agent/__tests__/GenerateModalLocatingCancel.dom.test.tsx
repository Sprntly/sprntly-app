/**
 * @vitest-environment jsdom
 *
 * Regression + behavior coverage for the "locating" phase's Cancel control.
 * For up to ~90s (LOCATE_POLL_TIMEOUT_MS) the async codebase-source
 * screen-resolve call runs while the modal's own body is the ONLY visible
 * surface — the full-page GenerationLoadingScreen overlay does not mount
 * until that resolve completes. Before this control existed, the only way
 * to dismiss during that window was the generic header Close (×).
 *
 * Uses jsdom + @testing-library/react, with the `_test*` injection props
 * (this file's existing convention, e.g. GenerateModalImageSteer.dom.test.tsx)
 * to bypass the async connector/repo/source fetch effects entirely.
 */
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: vi.fn(), toast: null }),
}))

vi.mock("../DesignAgentDrawer", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../DesignAgentDrawer")>()
  return { ...actual, runGenerateFlow: vi.fn().mockResolvedValue(undefined) }
})

import { GenerateModal } from "../GenerateModal"

const PRD_ID = 77

function baseProps(overrides: Record<string, unknown> = {}) {
  return {
    open: true,
    onClose: vi.fn(),
    prdId: PRD_ID,
    figmaFileKey: null,
    _testConnections: [],
    _testRepos: null,
    _testInitSource: "website" as const,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("GenerateModal — locating phase Cancel control", () => {
  it("test_generateModal_locating_renders_cancel_button", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        baseProps({ _testFlowPhase: "locating", onCancel }),
      ),
    )

    const btn = screen.getByTestId("proto-gen-cancel-btn")
    expect(btn.textContent).toBe("Cancel")
  })

  it("test_generateModal_locating_cancel_click_invokes_onCancel", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        baseProps({ _testFlowPhase: "locating", onCancel }),
      ),
    )

    fireEvent.click(screen.getByTestId("proto-gen-cancel-btn"))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("test_generateModal_locating_no_cancel_button_when_onCancel_omitted", () => {
    render(
      React.createElement(GenerateModal, baseProps({ _testFlowPhase: "locating" })),
    )

    expect(screen.queryByTestId("proto-gen-cancel-btn")).toBeNull()
  })

  it("test_generateModal_generating_does_not_render_own_cancel_button", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        baseProps({ _testFlowPhase: "generating", onCancel }),
      ),
    )

    expect(screen.queryByTestId("proto-gen-cancel-btn")).toBeNull()
  })

  it("test_generateModal_config_phase_no_cancel_button", () => {
    const onCancel = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        baseProps({ _testFlowPhase: "config", onCancel }),
      ),
    )

    expect(screen.queryByTestId("proto-gen-cancel-btn")).toBeNull()
  })

  it("test_generateModal_header_close_button_still_calls_only_onClose", () => {
    const onClose = vi.fn()
    const onCancel = vi.fn()
    render(
      React.createElement(
        GenerateModal,
        baseProps({ _testFlowPhase: "locating", onClose, onCancel }),
      ),
    )

    fireEvent.click(screen.getByLabelText("Close"))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onCancel).not.toHaveBeenCalled()
  })
})
