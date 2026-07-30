// @vitest-environment jsdom
//
// The confirmation gate between a proposed ticket rewrite and a real one.
//
// Sibling of JiraChangeConfirm.dom.test, guarding a sharper edge: this card
// REPLACES a description rather than setting a field, so the tests that matter
// are (a) rendering must not write, (b) the user can read the exact replacement
// text before agreeing to it, and (c) the write goes to the endpoint that owns
// the surface the ticket actually lives on.
import * as React from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { applyChange, saveDescription, showToast } = vi.hoisted(() => ({
  applyChange: vi.fn(),
  saveDescription: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock("../../../lib/api", () => ({
  jiraApi: { applyChange: (...a: unknown[]) => applyChange(...a) },
  ticketDataApi: { saveDescription: (...a: unknown[]) => saveDescription(...a) },
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast }),
}))

import { TicketChangeConfirm } from "../TicketChangeConfirm"

const SPRNTLY = {
  target: "sprntly" as const,
  ticket_key: "prd-42-abc123",
  title: "Checkout rewrite",
  description: "Rebuild checkout so a saved card pays in one tap.",
  acceptance_criteria: ["User can pay with a saved card"],
  preview: [
    "Description: replaces the current 120-character description with 47 characters",
    "Acceptance criteria: 2 → 1 item(s)",
  ],
}

const JIRA = {
  ...SPRNTLY,
  target: "jira" as const,
  ticket_key: "KAN-1033",
  acceptance_criteria: null,
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("TicketChangeConfirm", () => {
  it("shows the replacement text in full and writes NOTHING until confirmed", () => {
    render(<TicketChangeConfirm change={SPRNTLY} />)

    expect(document.body.textContent).toContain("prd-42-abc123")
    expect(document.body.textContent).toContain("Checkout rewrite")
    expect(document.body.textContent).toContain("Acceptance criteria: 2 → 1 item(s)")
    // The words being agreed to must be on screen, not just described — this
    // card destroys the current description, so a summary alone isn't consent.
    expect(document.body.textContent).toContain(
      "Rebuild checkout so a saved card pays in one tap.",
    )
    expect(document.body.textContent).toContain("User can pay with a saved card")

    expect(saveDescription).not.toHaveBeenCalled()
    expect(applyChange).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain("Nothing is written until you confirm")
  })

  it("writes a Sprntly ticket through the tickets endpoint", async () => {
    saveDescription.mockResolvedValue({ ok: true })
    const { getByText } = render(<TicketChangeConfirm change={SPRNTLY} />)

    await act(async () => { fireEvent.click(getByText("Confirm update")) })

    await waitFor(() => expect(saveDescription).toHaveBeenCalledTimes(1))
    expect(saveDescription).toHaveBeenCalledWith(
      "prd-42-abc123",
      "Rebuild checkout so a saved card pays in one tap.",
      ["User can pay with a saved card"],
    )
    expect(applyChange).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(document.body.textContent).toContain("Applied to prd-42-abc123"))
  })

  it("writes a Jira issue through the Jira endpoint instead", async () => {
    applyChange.mockResolvedValue({
      ok: true, issue_key: "KAN-1033", applied: ["fields"], failed: [],
    })
    const { getByText } = render(<TicketChangeConfirm change={JIRA} />)

    await act(async () => { fireEvent.click(getByText("Confirm update")) })

    await waitFor(() => expect(applyChange).toHaveBeenCalledTimes(1))
    expect(applyChange).toHaveBeenCalledWith({
      issue_key: "KAN-1033",
      fields: { description: "Rebuild checkout so a saved card pays in one tap." },
    })
    expect(saveDescription).not.toHaveBeenCalled()
  })

  it("cancel discards it without touching either surface", async () => {
    const { getByText } = render(<TicketChangeConfirm change={SPRNTLY} />)
    await act(async () => { fireEvent.click(getByText("Cancel")) })

    expect(saveDescription).not.toHaveBeenCalled()
    expect(applyChange).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain("nothing was written")
  })

  it("surfaces a failed write instead of pretending it landed", async () => {
    saveDescription.mockRejectedValue(new Error("Ticket service unreachable"))
    const { getByText } = render(<TicketChangeConfirm change={SPRNTLY} />)

    await act(async () => { fireEvent.click(getByText("Confirm update")) })

    await waitFor(() =>
      expect(document.body.textContent).toContain("Ticket service unreachable"))
    expect(document.body.textContent).not.toContain("Applied to")
    // Still offering the button: a transient failure is retryable without
    // re-asking the agent to work the whole rewrite out again.
    expect(getByText("Confirm update")).toBeTruthy()
  })

  it("reports a rejected Jira write rather than reporting success", async () => {
    applyChange.mockResolvedValue({
      ok: false, issue_key: "KAN-1033", applied: [], failed: ["fields"],
      fields: { ok: false, error: "description is not editable" },
    })
    const { getByText } = render(<TicketChangeConfirm change={JIRA} />)

    await act(async () => { fireEvent.click(getByText("Confirm update")) })

    await waitFor(() =>
      expect(document.body.textContent).toContain("description is not editable"))
    expect(document.body.textContent).not.toContain("Applied to")
  })
})
