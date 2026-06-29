// @vitest-environment jsdom
//
// Tests for the PRD "Send to Claude Code" action. The machine-readable
// Implementation Spec is no longer a viewable tab — it is generated ON DEMAND
// when the user hands the PRD to a coding agent. This button triggers that
// generation (showing a loading state), then copies the agent-ready spec to the
// clipboard.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../lib/api", () => ({
  prdApi: { sendToClaudeCode: vi.fn() },
}))

import { prdApi } from "../../../lib/api"
import { SendToClaudeCode } from "../SendToClaudeCode"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function setup() {
  const onToast = vi.fn()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.assign(navigator, { clipboard: { writeText } })
  render(React.createElement(SendToClaudeCode, { prdId: 7, onToast }))
  return { onToast, writeText }
}

describe("SendToClaudeCode", () => {
  it("triggers on-demand spec generation and copies the result to the clipboard", async () => {
    const { onToast, writeText } = setup()
    ;(prdApi.sendToClaudeCode as ReturnType<typeof vi.fn>).mockResolvedValue({
      llm_part: "# Implementation Spec\nWHEN x THE SYSTEM SHALL y.",
      cached: false,
    })

    fireEvent.click(screen.getByTestId("prd-send-claude"))

    await waitFor(() => expect(prdApi.sendToClaudeCode).toHaveBeenCalledWith(7))
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "# Implementation Spec\nWHEN x THE SYSTEM SHALL y.",
      ),
    )
    expect(onToast).toHaveBeenCalledWith(
      "Ready for Claude Code",
      expect.stringContaining("clipboard"),
    )
  })

  it("shows a loading label while the spec generates", async () => {
    setup()
    let resolve!: (v: unknown) => void
    ;(prdApi.sendToClaudeCode as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolve = r
      }),
    )

    const btn = screen.getByTestId("prd-send-claude")
    fireEvent.click(btn)

    await waitFor(() => expect(btn.textContent).toMatch(/Generating spec/i))
    expect((btn as HTMLButtonElement).disabled).toBe(true)

    resolve({ llm_part: "spec", cached: true })
    await waitFor(() => expect(btn.textContent).toMatch(/Send to Claude Code/i))
  })

  it("surfaces a failure toast and does not copy when generation fails", async () => {
    const { onToast, writeText } = setup()
    ;(prdApi.sendToClaudeCode as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("boom"),
    )

    fireEvent.click(screen.getByTestId("prd-send-claude"))

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith(
        "Couldn't generate spec",
        expect.any(String),
      ),
    )
    expect(writeText).not.toHaveBeenCalled()
  })

  it("handles an empty spec without copying", async () => {
    const { onToast, writeText } = setup()
    ;(prdApi.sendToClaudeCode as ReturnType<typeof vi.fn>).mockResolvedValue({
      llm_part: "",
      cached: false,
    })

    fireEvent.click(screen.getByTestId("prd-send-claude"))

    await waitFor(() =>
      expect(onToast).toHaveBeenCalledWith("Nothing to send", expect.any(String)),
    )
    expect(writeText).not.toHaveBeenCalled()
  })
})
