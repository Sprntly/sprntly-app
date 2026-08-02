// @vitest-environment jsdom
//
// The confirmation gate between a proposed Jira change and a real one.
//
// The agent can only PROPOSE — no backend path lets a model apply a change. This
// card is the other half of that contract, so the tests that matter are about
// what happens BEFORE a click: rendering a proposal must not write, and the user
// must be able to see exactly what would change and walk away from it.
import * as React from "react"
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const { applyChange, showToast } = vi.hoisted(() => ({
  applyChange: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock("../../../lib/api", () => ({ jiraApi: { applyChange: (...a: unknown[]) => applyChange(...a) } }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ showToast }),
}))

import { JiraChangeConfirm } from "../JiraChangeConfirm"

const CHANGE = {
  issue_key: "KAN-1033",
  summary: "Build a car driving feature",
  fields: { duedate: "2028-08-31" },
  to_status: "",
  comment: "",
  preview: ["Due date: — → 2028-08-31"],
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("JiraChangeConfirm", () => {
  it("shows what would change and writes NOTHING until confirmed", async () => {
    render(<JiraChangeConfirm change={CHANGE} />)

    expect(document.body.textContent).toContain("KAN-1033")
    expect(document.body.textContent).toContain("Due date: — → 2028-08-31")
    // The whole safety model in one assertion: rendering a proposal must never
    // reach Jira. A chat command is a guess about intent, and Jira has no undo.
    expect(applyChange).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain("Nothing is written to Jira until you confirm")
  })

  it("applies exactly the proposed change on confirm", async () => {
    applyChange.mockResolvedValue({
      ok: true, issue_key: "KAN-1033", applied: ["fields"], failed: [],
    })
    const { getByText } = render(<JiraChangeConfirm change={CHANGE} />)

    await act(async () => { fireEvent.click(getByText("Confirm change")) })

    await waitFor(() => expect(applyChange).toHaveBeenCalledTimes(1))
    // Only the parts that were proposed are sent — an empty status or comment
    // must not travel as a field the user never agreed to.
    expect(applyChange).toHaveBeenCalledWith({
      issue_key: "KAN-1033",
      fields: { duedate: "2028-08-31" },
    })
    await waitFor(() =>
      expect(document.body.textContent).toContain("Applied to KAN-1033"))
  })

  it("cancel discards it without touching Jira", async () => {
    const { getByText } = render(<JiraChangeConfirm change={CHANGE} />)
    await act(async () => { fireEvent.click(getByText("Cancel")) })

    expect(applyChange).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain("nothing was written")
  })

  it("reports a PARTIAL failure rather than implying it all worked", async () => {
    applyChange.mockResolvedValue({
      ok: false, issue_key: "KAN-1033",
      applied: ["fields"], failed: ["status"],
      status: { ok: false, error: "not reachable from In Review" },
    })
    const { getByText } = render(
      <JiraChangeConfirm change={{ ...CHANGE, to_status: "Released" }} />,
    )

    await act(async () => { fireEvent.click(getByText("Confirm change")) })

    await waitFor(() =>
      expect(document.body.textContent).toContain("Partly applied"))
    expect(document.body.textContent).toContain("not reachable from In Review")
  })

  it("surfaces a failed request instead of pretending the change landed", async () => {
    applyChange.mockRejectedValue(new Error("Jira unreachable"))
    const { getByText } = render(<JiraChangeConfirm change={CHANGE} />)

    await act(async () => { fireEvent.click(getByText("Confirm change")) })

    await waitFor(() =>
      expect(document.body.textContent).toContain("Jira unreachable"))
    // Still offering the button matters: the user can retry a transient failure
    // without re-asking the agent to work the request out again.
    expect(getByText("Confirm change")).toBeTruthy()
  })
})
