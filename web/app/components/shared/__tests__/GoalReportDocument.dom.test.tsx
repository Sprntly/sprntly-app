// @vitest-environment jsdom
//
// The Goal Analysis report, open as an editable document.
//
// ONE THING IS ACTUALLY BEING GUARDED HERE: that an edited report is
// distinguishable from an untouched one, on screen, without reading the prose.
//
// A run's whole claim over asking a general model the same question is that it
// is reproducible — every finding traces to claim ids and source documents, and
// the same corpus gives the same ranking. An edited report is not that. If the
// two looked alike, every report would carry the reproducibility claim and only
// some would deserve it, which is worse than not offering editing at all.
//
// The second test is the control for the first: a marker that renders whether
// or not anything was edited proves nothing about the reports that were.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// The editor itself is `DocumentTab`, which has its own suite. Stubbed here so
// this file tests the frame and the banner — the two things it owns — rather
// than re-testing TipTap through a lazy chunk.
vi.mock("../DocumentTab", () => ({
  DocumentTab: ({ documentId }: { documentId: number }) => (
    <div data-testid="stub-document-tab">editing {documentId}</div>
  ),
}))

import { GoalReportDocument } from "../GoalReportDocument"

const DOC = {
  run_id: 7,
  id: 41,
  kind: "goal_analysis",
  title: "Goal analysis: raise renewal rate",
  status: "ready" as const,
  body_html: "<h1>Goal analysis</h1>",
  version: 2,
  updated_at: null,
  updated_by: null,
  detached: false,
}

afterEach(cleanup)

describe("the detached marker", () => {
  it("says an edited report is no longer regenerated from the run", async () => {
    render(<GoalReportDocument doc={{ ...DOC, detached: true }} />)
    const banner = screen.getByTestId("goal-report-detached")
    expect(banner.textContent).toContain("Edited")
    expect(banner.textContent).toContain("no longer regenerated from the run")
    // And it says the analysis survived, because the fear an edit creates is
    // "have I just overwritten the findings?" — which is exactly what has NOT
    // happened.
    expect(banner.textContent).toMatch(/analysis behind it is unchanged/i)
  })

  it("does not mark an untouched report as edited", async () => {
    // The control. A banner that fires before anything has happened is one
    // people learn to skip, and then it is not there when it matters.
    render(<GoalReportDocument doc={DOC} />)
    expect(screen.queryByTestId("goal-report-detached")).toBeNull()
    expect(screen.getByTestId("goal-report-attached")).toBeTruthy()
  })

  it("offers the way back to the run from a detached report", async () => {
    // A marker with no route back is a dead end: the reader is told the
    // findings still exist and given no way to reach them.
    const onBack = vi.fn()
    render(<GoalReportDocument doc={{ ...DOC, detached: true }} onBack={onBack} />)
    fireEvent.click(screen.getByTestId("goal-report-back"))
    expect(onBack).toHaveBeenCalledOnce()
  })
})

describe("the editor", () => {
  it("mounts the shared document editor on the report's own row", async () => {
    // The report IS a custom artifact, and reusing that editor is the whole
    // argument for storing it as one. A second editor here would be the drift
    // the shared one exists to prevent.
    render(<GoalReportDocument doc={DOC} />)
    await waitFor(() =>
      expect(screen.getByTestId("stub-document-tab").textContent).toBe(
        "editing 41",
      ),
    )
  })

  it("offers Save as document from an edited report", async () => {
    const onSaveCopy = vi.fn()
    render(
      <GoalReportDocument
        doc={{ ...DOC, detached: true }}
        onSaveCopy={onSaveCopy}
      />,
    )
    fireEvent.click(screen.getByTestId("goal-report-save-copy"))
    expect(onSaveCopy).toHaveBeenCalledOnce()
  })

  it("disables the copy action while one is already in flight", async () => {
    // Both write to the same run. Letting the second fire while the first is
    // going is how you get a copy of a report that is mid-creation.
    const onSaveCopy = vi.fn()
    render(
      <GoalReportDocument
        doc={{ ...DOC, detached: true }}
        onSaveCopy={onSaveCopy}
        busy
      />,
    )
    fireEvent.click(screen.getByTestId("goal-report-save-copy"))
    expect(onSaveCopy).not.toHaveBeenCalled()
  })
})
