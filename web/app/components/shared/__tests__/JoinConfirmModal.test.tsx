// @vitest-environment jsdom
//
// JoinConfirmModal wraps the existing ConfirmDialog shared shell (real
// role="dialog"/aria-modal, escape/backdrop-cancel, busy-locked confirm —
// nothing hand-rolled). Confirming calls artifactShareApi.join exactly once,
// fires a Toast with the sharer's name, then hard-reloads to the real app.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const showToastMock = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast: showToastMock }),
}))

const refreshMock = vi.fn().mockResolvedValue(undefined)
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ refresh: refreshMock }),
}))

const joinMock = vi.fn()
vi.mock("../../../lib/artifactShareApi", () => ({
  artifactShareApi: { join: (...a: unknown[]) => joinMock(...a) },
}))

import { JoinConfirmModal } from "../JoinConfirmModal"

const defaultProps = {
  open: true,
  token: "tok-1",
  artifactId: 482,
  sharerName: "Priya Shah",
  onClose: vi.fn(),
}

let assignSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  assignSpy = vi.fn()
  // jsdom's window.location.assign throws "not implemented" — replace it
  // with a spy for the duration of each test.
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign: assignSpy },
    writable: true,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("JoinConfirmModal", () => {
  it("renders as a real dialog (reused ConfirmDialog shell)", () => {
    render(<JoinConfirmModal {...defaultProps} />)
    const dialog = screen.getByRole("dialog")
    expect(dialog.getAttribute("aria-modal")).toBe("true")
  })

  it("test_join_confirm_modal_calls_join_and_fires_attributed_toast_then_reloads — AC18", async () => {
    joinMock.mockResolvedValue({
      sharer_name: "Priya Shah",
      owning_company_name: "Acme Co",
      workspace_id: "ws-1",
    })
    render(<JoinConfirmModal {...defaultProps} />)

    fireEvent.click(screen.getByRole("button", { name: /join workspace/i }))

    await waitFor(() => {
      expect(joinMock).toHaveBeenCalledTimes(1)
    })
    expect(joinMock).toHaveBeenCalledWith("tok-1")
    expect(showToastMock).toHaveBeenCalled()
    expect(showToastMock.mock.calls[0].join(" ")).toContain("Priya Shah")
    expect(refreshMock).toHaveBeenCalled()
    expect(assignSpy).toHaveBeenCalledWith("/?prd=482")
  })

  it("test_join_confirm_modal_escape_cancels_without_join_call — AC19", () => {
    const onClose = vi.fn()
    render(<JoinConfirmModal {...defaultProps} onClose={onClose} />)
    fireEvent.keyDown(window, { key: "Escape" })
    expect(onClose).toHaveBeenCalled()
    expect(joinMock).not.toHaveBeenCalled()
  })

  it("test_join_confirm_modal_backdrop_click_cancels_without_join_call — AC19", () => {
    const onClose = vi.fn()
    render(<JoinConfirmModal {...defaultProps} onClose={onClose} />)
    const overlay = document.querySelector(".modal-overlay") as HTMLElement
    fireEvent.click(overlay)
    expect(onClose).toHaveBeenCalled()
    expect(joinMock).not.toHaveBeenCalled()
  })

  it("test_join_confirm_modal_debounces_double_confirm — AC20", async () => {
    let resolveJoin: (v: unknown) => void = () => {}
    joinMock.mockReturnValue(
      new Promise((resolve) => {
        resolveJoin = resolve
      }),
    )
    render(<JoinConfirmModal {...defaultProps} />)
    const btn = screen.getByRole("button", { name: /join workspace/i })
    fireEvent.click(btn)
    fireEvent.click(btn)
    fireEvent.click(btn)

    expect(joinMock).toHaveBeenCalledTimes(1)
    resolveJoin({ sharer_name: "Priya Shah", owning_company_name: "Acme Co", workspace_id: "ws-1" })
    await waitFor(() => expect(assignSpy).toHaveBeenCalled())
  })

  it("renders nothing when closed", () => {
    render(<JoinConfirmModal {...defaultProps} open={false} />)
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})
